// A real client of Angula's own socket protocol (subscribe, publish, presence,
// history — see services/angula/routes/__init__.py), tunnelled through
// Studio's operator-only WebSocket bridge. The browser never holds a project
// key: it fetches a short-lived ticket over a normal, session-checked request,
// then presents that ticket as the one socket opens. Reconnects with backoff
// and re-subscribes to whatever channels the UI still has open.
import { useCallback, useEffect, useRef, useState } from "react";

let refCounter = 0;

export function useRealtimeSocket(project, env) {
  const [status, setStatus] = useState("connecting"); // connecting | open | closed
  const [stats, setStats] = useState({ sent: 0, received: 0 });
  const socketRef = useRef(null);
  const listenersRef = useRef(new Set());
  const subsRef = useRef(new Map());
  const pendingRef = useRef(new Map());

  const send = useCallback((msg) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(msg));
    setStats((s) => ({ ...s, sent: s.sent + 1 }));
    return true;
  }, []);

  const request = useCallback((msg, timeout = 8000) => new Promise((resolve, reject) => {
    const ref = String(++refCounter);
    const timer = setTimeout(() => {
      pendingRef.current.delete(ref);
      reject(new Error("timed out waiting for the realtime service"));
    }, timeout);
    pendingRef.current.set(ref, { resolve, timer });
    if (!send({ ...msg, ref })) {
      clearTimeout(timer);
      pendingRef.current.delete(ref);
      reject(new Error("not connected"));
    }
  }), [send]);

  /** Listen to every inbound message (channel messages, presence, history). Returns an unsubscribe function. */
  const on = useCallback((fn) => {
    listenersRef.current.add(fn);
    return () => listenersRef.current.delete(fn);
  }, []);

  const subscribe = useCallback((channel, opts = {}) => {
    subsRef.current.set(channel, opts);
    return request({ type: "subscribe", channel, since: 0, ...opts });
  }, [request]);

  const unsubscribe = useCallback((channel) => {
    subsRef.current.delete(channel);
    return request({ type: "unsubscribe", channel });
  }, [request]);

  const publish = useCallback((channel, event, payload) => request({ type: "publish", channel, event, payload }), [request]);
  const trackPresence = useCallback((channel, meta) => request({ type: "presence", channel, meta }), [request]);
  const history = useCallback((channel, limit = 50) => request({ type: "history", channel, limit }), [request]);

  useEffect(() => {
    let stopped = false;
    let socket;
    let pingTimer;
    let retryTimer;
    let backoff = 500;

    async function connect() {
      if (stopped) return;
      setStatus("connecting");
      let ticket;
      try {
        const r = await fetch(`/projects/${project}/${env}/realtime/ticket`, { credentials: "same-origin" });
        if (!r.ok) throw new Error(String(r.status));
        ({ ticket } = await r.json());
      } catch {
        if (!stopped) retryTimer = setTimeout(connect, 2000);
        return;
      }
      if (stopped) return;
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${scheme}//${location.host}/studio/ws/realtime?ticket=${encodeURIComponent(ticket)}`);
      socketRef.current = socket;
      socket.onopen = () => {
        backoff = 500;
        setStatus("open");
        for (const [channel, opts] of subsRef.current) {
          socket.send(JSON.stringify({ type: "subscribe", channel, since: 0, ...opts }));
        }
        pingTimer = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
        }, 20000);
      };
      socket.onmessage = (event) => {
        setStats((s) => ({ ...s, received: s.received + 1 }));
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if (data.ref && pendingRef.current.has(data.ref)) {
          const { resolve, timer } = pendingRef.current.get(data.ref);
          clearTimeout(timer);
          pendingRef.current.delete(data.ref);
          resolve(data);
        }
        listenersRef.current.forEach((fn) => fn(data));
      };
      socket.onclose = () => {
        clearInterval(pingTimer);
        socketRef.current = null;
        if (stopped) return;
        setStatus("closed");
        backoff = Math.min(backoff * 1.6, 8000);
        retryTimer = setTimeout(connect, backoff);
      };
      socket.onerror = () => {
        try { socket.close(); } catch { /* already closing */ }
      };
    }

    connect();
    return () => {
      stopped = true;
      clearInterval(pingTimer);
      clearTimeout(retryTimer);
      socketRef.current = null;
      try { socket?.close(); } catch { /* already closed */ }
    };
  }, [project, env]);

  return { status, stats, on, subscribe, unsubscribe, publish, trackPresence, history };
}
