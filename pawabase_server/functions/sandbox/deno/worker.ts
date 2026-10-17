/**
 * A TypeScript sandbox worker: the Deno counterpart of `worker.py`, speaking the same JSON-lines protocol on its pipes.
 *
 * host → worker: `init` (where the deployment is), `invoke` (one call), `reply` (the answer to a runtime call), `stop`.
 * worker → host: `ready` (the functions it found), `call` (a ctx.runtime method), `done` (the end of an invocation).
 *
 * Standard output is the protocol; `console.*` goes to standard error, which the host records. With `--describe <artifact>` it loads the
 * deployment, prints its functions' specs as one JSON line, and exits: how the server learns a TypeScript deployment's functions.
 */

import { Context, decode, encode, FunctionError, registry, Runtime } from "./pawabase.ts";
import { discover, installServeShim } from "./http_support.ts";

// An HTTP function's module may call Deno.serve when it is imported (describe); it must never listen.
installServeShim();

const out = new TextEncoder();

function writeAll(data: Uint8Array) {
  let offset = 0;
  while (offset < data.length) offset += Deno.stdout.writeSync(data.subarray(offset));
}

function send(message: Record<string, unknown>) {
  writeAll(out.encode(JSON.stringify(message) + "\n"));
}

// The function's own output must never reach the protocol stream.
const toStderr = (...parts: unknown[]) => {
  const line = parts.map((p) => (typeof p === "string" ? p : Deno.inspect(p))).join(" ") + "\n";
  Deno.stderr.writeSync(out.encode(line));
};
for (const name of ["log", "info", "warn", "error", "debug"] as const) (console as any)[name] = toStderr;

async function load(artifact: string, allowEmpty = false): Promise<string[]> {
  const errors: string[] = [];
  const folder = `${artifact}/functions`;
  const files: string[] = [];
  for await (const entry of Deno.readDir(folder)) {
    if (entry.isFile && /\.(ts|js|mts|mjs)$/.test(entry.name) && !entry.name.endsWith(".d.ts") && !entry.name.includes(".test.")) {
      files.push(entry.name);
    }
  }
  files.sort();
  for (const file of files) {
    try {
      await import(`file://${folder}/${file}`);
    } catch (error) {
      errors.push(`functions/${file}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if (!files.length && !allowEmpty) errors.push("The bundle must contain functions/*.ts or functions/<name>/index.ts.");
  return errors;
}

function specs(artifact: string) {
  const root = `file://${artifact}/`;
  return [...registry.values()].map(({ handler: _handler, location, ...spec }) => ({
    ...spec,
    file: location?.url.startsWith(root) ? decodeURIComponent(location.url.slice(root.length)) : null,
    line: location?.line ?? null,
  }));
}

function where(error: unknown, artifact: string) {
  const stack = error instanceof Error && error.stack ? error.stack.split("\n") : [];
  const frames = [];
  for (const line of stack) {
    const match = line.match(/(?:at (?:(.+?) \()?)file:\/\/(.+?):(\d+):\d+\)?$/);
    if (match && match[2].startsWith(artifact)) {
      frames.push({ file: match[2].slice(artifact.length + 1), line: Number(match[3]), function: match[1] || "<module>", code: null });
    }
  }
  return frames;
}

// ── describe ─────────────────────────────────────────────────────────────

if (Deno.args[0] === "--describe") {
  const artifact = Deno.args[1];
  const errors = await load(artifact, true);
  const found = await discover(artifact);
  if (!registry.size && !found.functions.length && !errors.length && !found.errors.length) {
    errors.push("The bundle must contain functions/*.ts or functions/<name>/index.ts.");
  }
  send({ specs: specs(artifact), http: found.functions, errors: [...errors, ...found.errors] });
  Deno.exit(0);
}

// ── serve ────────────────────────────────────────────────────────────────

let loaded: { artifact: string; env: string; branch: string } | null = null;
const pending = new Map<number, (reply: any) => void>();
let counter = 0;

function call(method: string, args: unknown[], kwargs: Record<string, unknown>): Promise<unknown> {
  const id = ++counter;
  return new Promise((resolve, reject) => {
    pending.set(id, (reply) => {
      if (reply.ok) resolve(decode(reply.body?.result));
      else {
        const body = reply.body || {};
        reject(new FunctionError(body.message || body.detail || `${method} failed`, { status: reply.status || 500, code: body.error || body.code || "runtime_error", details: body.details }));
      }
    });
    send({ t: "call", id, method, args: encode(args), kwargs: encode(kwargs) });
  });
}

async function invoke(message: any) {
  const id = message.id;
  const spec = registry.get(message.function);
  if (!spec || !loaded) {
    send({ t: "done", id, outcome: "missing" });
    return;
  }
  const logs: Record<string, unknown>[] = [];
  const context: Context = {
    input: decode(message.input),
    auth: message.auth || {},
    env: loaded.env,
    project: message.project || "",
    branch: loaded.branch,
    trigger: message.trigger || "http",
    request: decode(message.request),
    runtime: new Runtime(call),
    log: (...parts) => logs.push({ message: parts.map((p) => (typeof p === "string" ? p : JSON.stringify(p))).join(" ") }),
  };
  const reply: Record<string, unknown> = { t: "done", id };
  let timer: number | undefined;
  try {
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("__timeout__")), spec.timeout * 1000);
    });
    const result = await Promise.race([Promise.resolve().then(() => spec.handler(context)), timeout]);
    reply.outcome = "ok";
    reply.result = encode(result);
  } catch (error) {
    if (error instanceof Error && error.message === "__timeout__") reply.outcome = "timeout";
    else if (error instanceof FunctionError) {
      Object.assign(reply, { outcome: "error", status: error.status, code: error.code, message: error.message, details: encode(error.details) });
    } else {
      const err = error instanceof Error ? error : new Error(String(error));
      Object.assign(reply, { outcome: "failed", kind: err.name || "Error", message: err.message, where: where(err, loaded.artifact), hint: null });
    }
  } finally {
    clearTimeout(timer);
  }
  reply.logs = encode(logs);
  send(reply);
}

const decoder = new TextDecoder();
let buffer = "";
for await (const chunk of Deno.stdin.readable) {
  buffer += decoder.decode(chunk, { stream: true });
  let newline;
  while ((newline = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.t === "reply") {
      pending.get(message.id)?.(message);
      pending.delete(message.id);
    } else if (message.t === "init") {
      loaded = { artifact: message.artifact, env: message.env, branch: message.branch };
      const errors = await load(message.artifact);
      send({ t: "ready", functions: [...registry.keys()], errors, confinement: { mode: Deno.env.get("PAWABASE_CONFINED") || "off", runtime: "deno" } });
    } else if (message.t === "invoke") {
      invoke(message);
    } else if (message.t === "stop") {
      Deno.exit(0);
    }
  }
}
