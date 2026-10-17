/**
 * What both TypeScript workers need to know about HTTP functions: how a module becomes one (`serve()` from the kit, or `Deno.serve`), and
 * how the HTTP functions of a deployment are found.
 *
 * An HTTP function is a folder, `functions/<name>/`, with an entry file `index.ts` (or `.js`, `.mts`, `.mjs`). A folder whose name starts
 * with `_` (`functions/_shared/`) or `.` is code the functions share, never a function. The entry may export `config` (see
 * `HttpFunctionConfig` in the kit).
 *
 * `Deno.serve` is replaced before any user module is imported: inside the platform a function never listens on a socket (it has none), so
 * the call only records the handler, and the worker hands it each request.
 */

import { __http as http } from "./pawabase.ts";

export const ENTRIES = ["index.ts", "index.mts", "index.js", "index.mjs"];
export const NAME = /^[a-z0-9][a-z0-9_-]{0,62}$/;
/** Names a function folder cannot have: they are path segments the platform itself uses under `/functions/v1/`. */
export const RESERVED = new Set(["public", "v1", "health", "status"]);

type DenoServeHandler = (request: Request, info: any) => Response | Promise<Response>;

/** Make `Deno.serve(...)` record its handler instead of listening. Every argument shape Deno accepts is understood. */
export function installServeShim(): void {
  const replacement = (...args: any[]) => {
    let handler: DenoServeHandler | undefined;
    for (const arg of args) {
      if (typeof arg === "function") handler = arg;
      else if (arg && typeof arg === "object" && typeof arg.handler === "function") handler = arg.handler;
    }
    if (!handler) throw new TypeError("Deno.serve() needs a handler");
    if (http.current) throw new Error("Deno.serve() was called twice: an HTTP function has one handler");
    http.current = { kind: "deno", handler };
    let finish: () => void = () => {};
    const finished = new Promise<void>((resolve) => (finish = resolve));
    return {
      finished,
      addr: { transport: "tcp", hostname: "0.0.0.0", port: 0 },
      ref() {},
      unref() {},
      shutdown: async () => finish(),
      [Symbol.asyncDispose]: async () => finish(),
    };
  };
  Object.defineProperty(Deno, "serve", { value: replacement, configurable: true, writable: true });
}

export interface Discovered {
  name: string;
  entry: string;
  config: Record<string, unknown>;
  /** `pawabase` (the kit's `serve`) or `deno` (`Deno.serve`). */
  style: string | null;
}

/** The HTTP function folders of a deployment, sorted, with their entry file (no module is imported). */
export async function folders(artifact: string): Promise<{ found: { name: string; entry: string }[]; errors: string[] }> {
  const found: { name: string; entry: string }[] = [];
  const errors: string[] = [];
  const root = `${artifact}/functions`;
  let entries: Deno.DirEntry[] = [];
  try {
    for await (const entry of Deno.readDir(root)) entries.push(entry);
  } catch {
    return { found, errors };
  }
  entries.sort((a, b) => (a.name < b.name ? -1 : 1));
  for (const entry of entries) {
    if (!entry.isDirectory || entry.name.startsWith("_") || entry.name.startsWith(".")) continue;
    let file: string | null = null;
    for (const candidate of ENTRIES) {
      try {
        if ((await Deno.stat(`${root}/${entry.name}/${candidate}`)).isFile) {
          file = candidate;
          break;
        }
      } catch {
        // not this one
      }
    }
    if (!file) continue; // a folder of helpers without an entry file
    if (!NAME.test(entry.name)) {
      errors.push(`functions/${entry.name}: an HTTP function's folder name is lowercase letters, digits, - and _ (at most 63 characters).`);
      continue;
    }
    if (RESERVED.has(entry.name)) {
      errors.push(`functions/${entry.name}: "${entry.name}" is reserved by the platform; rename the folder.`);
      continue;
    }
    found.push({ name: entry.name, entry: `functions/${entry.name}/${file}` });
  }
  return { found, errors };
}

/** Import one HTTP function's entry and report its handler style and `config`. The registration is cleared again afterwards. */
export async function inspect(artifact: string, name: string, entry: string): Promise<Discovered> {
  http.current = null;
  const module = await import(`file://${artifact}/${entry}`);
  const style = http.current ? (http.current as { kind: string }).kind : null;
  http.current = null;
  const config = module.config && typeof module.config === "object" ? JSON.parse(JSON.stringify(module.config)) : {};
  return { name, entry, config, style };
}

/** Every HTTP function of a deployment, imported one after another, with what went wrong for each that failed. */
export async function discover(artifact: string): Promise<{ functions: Discovered[]; errors: string[] }> {
  const { found, errors } = await folders(artifact);
  const functions: Discovered[] = [];
  for (const { name, entry } of found) {
    try {
      const described = await inspect(artifact, name, entry);
      if (!described.style) {
        errors.push(`${entry}: it never calls serve() or Deno.serve(), so it answers nothing.`);
        continue;
      }
      functions.push(described);
    } catch (error) {
      errors.push(`${entry}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  return { functions, errors };
}
