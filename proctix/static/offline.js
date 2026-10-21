/* AES-GCM protects persisted bytes. Same-origin script with database access can still use the key.
   This is crash recovery and confidentiality at rest, not client attestation or proof of capture. */
class EvidenceQueue {
  constructor(scope) {
    this.scope = scope;
    this.running = false;
    this.onstatus = () => {};
  }
  async init() {
    this.db = await new Promise((resolve, reject) => {
      const r = indexedDB.open("proctix-v2", 2);
      r.onupgradeneeded = () => {
        if (!r.result.objectStoreNames.contains("keys"))
          r.result.createObjectStore("keys");
        const items = r.result.objectStoreNames.contains("items")
          ? r.transaction.objectStore("items")
          : r.result.createObjectStore("items", { keyPath: "id" });
        if (!items.indexNames.contains("scope"))
          items.createIndex("scope", "scope");
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    this.key = await this.get("keys", this.scope);
    if (!this.key) {
      this.key = await crypto.subtle.generateKey(
        { name: "AES-GCM", length: 256 },
        false,
        ["encrypt", "decrypt"],
      );
      try {
        await this.op("keys", "readwrite", (store) =>
          store.add(this.key, this.scope),
        );
      } catch (error) {
        if (error.name !== "ConstraintError") throw error;
        this.key = await this.get("keys", this.scope);
      }
    }
    return this;
  }
  async op(store, mode, fn) {
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(store, mode);
      let result;
      const request = fn(tx.objectStore(store));
      request.onsuccess = () => {
        result = request.result;
      };
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () =>
        reject(tx.error || new Error("Local storage transaction failed"));
    });
  }
  get(s, k) {
    return this.op(s, "readonly", (x) => x.get(k));
  }
  put(s, v, k) {
    return this.op(s, "readwrite", (x) =>
      k === undefined ? x.put(v) : x.put(v, k),
    );
  }
  del(s, k) {
    return this.op(s, "readwrite", (x) => x.delete(k));
  }
  async all() {
    return (
      await this.op("items", "readonly", (x) =>
        x.index("scope").getAll(this.scope, 100),
      )
    ).sort((a, b) => a.created - b.created || a.order - b.order);
  }
  async encrypt(bytes, id) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = await crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv,
        additionalData: new TextEncoder().encode(this.scope + ":" + id),
      },
      this.key,
      bytes,
    );
    return { iv, data };
  }
  async decrypt(item) {
    return crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: item.iv,
        additionalData: new TextEncoder().encode(this.scope + ":" + item.id),
      },
      this.key,
      item.data,
    );
  }
  async enqueue(url, blob, order = 0) {
    const estimate = await navigator.storage?.estimate?.();
    if (estimate?.quota && estimate.usage > estimate.quota * 0.9)
      throw new Error(
        "Local recovery storage is almost full; restore connectivity or contact your proctor.",
      );
    const id = crypto.randomUUID();
    const sealed = await this.encrypt(await blob.arrayBuffer(), id);
    await this.put("items", {
      id,
      scope: this.scope,
      url,
      mime: blob.type,
      created: Date.now(),
      order,
      ...sealed,
    });
    await this.report();
  }
  async snapshot(value) {
    // Serialize writes so slower encryption of an older edit cannot overwrite a newer recovery copy.
    this.snapshotWrite = (this.snapshotWrite || Promise.resolve()).then(
      async () => {
        const id = this.scope + ":answers";
        const sealed = await this.encrypt(
          new TextEncoder().encode(JSON.stringify(value)),
          id,
        );
        await this.put("items", {
          id,
          scope: this.scope,
          type: "answers",
          created: Date.now(),
          order: 0,
          ...sealed,
        });
      },
    );
    return this.snapshotWrite;
  }
  async readSnapshot() {
    const item = await this.get("items", this.scope + ":answers");
    return item
      ? JSON.parse(new TextDecoder().decode(await this.decrypt(item)))
      : null;
  }
  async clearSnapshot() {
    await this.snapshotWrite;
    await this.del("items", this.scope + ":answers");
  }
  async report() {
    const result = await new Promise((resolve, reject) => {
      const tx = this.db.transaction("items", "readonly");
      let count = 0,
        bytes = 0;
      const request = tx
        .objectStore("items")
        .index("scope")
        .openCursor(IDBKeyRange.only(this.scope));
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor) {
          if (cursor.value.type !== "answers") {
            count++;
            bytes += cursor.value.data.byteLength;
          }
          cursor.continue();
        }
      };
      tx.oncomplete = () => resolve({ count, bytes });
      tx.onerror = () => reject(tx.error);
    });
    this.onstatus(result.count, result.bytes);
  }
  async flush(send, accept = () => true) {
    if (Date.now() < (this.retryAt || 0)) return;
    if (this.running) return this.running;
    this.running = (async () => {
      while (true) {
        const batch = (await this.all()).filter(
          (item) => item.type !== "answers" && accept(item),
        );
        if (!batch.length) break;
        for (const item of batch) {
          const data = await this.decrypt(item);
          try {
            await send(item.url, {
              method: "POST",
              headers: { "Content-Type": item.mime },
              body: data,
            });
          } catch (error) {
            this.retryAt =
              Date.now() + Math.max(5, error.retryAfter || 5) * 1000;
            await this.report();
            throw error;
          }
          await this.del("items", item.id);
        }
      }
      await this.report();
    })();
    try {
      return await this.running;
    } finally {
      this.running = false;
    }
  }
  async clear() {
    while (true) {
      const batch = await this.all();
      if (!batch.length) break;
      for (const item of batch) await this.del("items", item.id);
    }
    await this.del("keys", this.scope);
  }
}
window.EvidenceQueue = EvidenceQueue;
