// Mail's calls that are more than one request: images for emails, and files as attachments.

import { envPath, get, post, uploadObject } from "../../../lib/api";

/** Images in emails live in one public bucket, created the first time someone uploads one. */
export const ASSET_BUCKET = "mail-assets";

async function ensureAssetBucket(env) {
  const buckets = await get(envPath(env, "/buckets"));
  if ((buckets?.data || []).some((b) => b.name === ASSET_BUCKET)) return;
  await post(envPath(env, "/buckets"), {
    name: ASSET_BUCKET,
    description: "Images used in emails. Public, because mail clients load them without signing in.",
    public: true,
    accepts: ["image/*"],
    max_bytes: 5 * 1024 * 1024,
    signed_uploads: false,
  });
}

/** Upload an image for an email and return its public address. */
export async function uploadImage(env, file) {
  if (!file.type.startsWith("image/")) throw new Error("Choose an image file (PNG, JPEG, GIF or WebP).");
  if (file.size > 5 * 1024 * 1024) throw new Error("Images in emails should be under 5 MB.");
  await ensureAssetBucket(env);
  const safe = file.name.toLowerCase().replace(/[^a-z0-9.]+/g, "-").replace(/^-+|-+$/g, "") || "image";
  const stamp = new Date().toISOString().slice(0, 10);
  const result = await uploadObject(env, ASSET_BUCKET, `${stamp}/${Date.now().toString(36)}-${safe}`, file);
  return result.url;
}

/** Ask for an image file and upload it. Resolves to the address, or null if nothing was chosen. */
export function pickAndUpload(env) {
  return new Promise((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return resolve(null);
      try { resolve(await uploadImage(env, file)); } catch (error) { reject(error); }
    };
    input.click();
  });
}

/** A file as an attachment: `{filename, content_type, content}` with base64 content. */
export function attachmentFrom(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ filename: file.name, content_type: file.type || "application/octet-stream", content: String(reader.result).split(",")[1] || "", size: file.size });
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function download(filename, text, type = "text/html") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
