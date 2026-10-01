import { describe, expect, it } from "vitest";
import { ModalError, modalClient } from "./modal";

const CFG = { modalBaseUrl: "https://m.test", token: "tok", run: "chat-v3" };

function fakeFetch(res: () => Response) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const f = (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return res();
  }) as typeof fetch;
  return { f, calls };
}

const json = (status: number, body: unknown) => () =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("modalClient.spawn", () => {
  it("posts history and run with a bearer token and returns the call id", async () => {
    const { f, calls } = fakeFetch(json(200, { id: "fc-1" }));
    const id = await modalClient(CFG, f).spawn([{ me: true, text: "hi" }]);
    expect(id).toBe("fc-1");
    expect(calls[0].url).toBe("https://m.test/generate");
    expect(calls[0].init?.method).toBe("POST");
    expect(new Headers(calls[0].init?.headers).get("authorization")).toBe("Bearer tok");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      history: [{ me: true, text: "hi" }],
      run: "chat-v3",
    });
  });

  it("throws ModalError on a non-2xx", async () => {
    const { f } = fakeFetch(json(400, { detail: "unknown run" }));
    await expect(modalClient(CFG, f).spawn([{ me: true, text: "hi" }])).rejects.toBeInstanceOf(ModalError);
    await expect(modalClient(CFG, f).spawn([{ me: true, text: "hi" }])).rejects.toMatchObject({ status: 400 });
  });

  it("throws ModalError when the id is missing", async () => {
    const { f } = fakeFetch(json(200, {}));
    await expect(modalClient(CFG, f).spawn([{ me: true, text: "hi" }])).rejects.toBeInstanceOf(ModalError);
  });
});

describe("modalClient.result", () => {
  it("maps 202 to pending and encodes the id", async () => {
    const { f, calls } = fakeFetch(json(202, { pending: true }));
    expect(await modalClient(CFG, f).result("fc-1")).toEqual({ pending: true });
    expect(calls[0].url).toBe("https://m.test/result?id=fc-1");
    expect(new Headers(calls[0].init?.headers).get("authorization")).toBe("Bearer tok");
  });

  it("returns the reply on 200", async () => {
    const { f } = fakeFetch(json(200, { reply: "lol", seconds: 3.2 }));
    expect(await modalClient(CFG, f).result("fc-1")).toEqual({ pending: false, reply: "lol" });
  });

  it("throws ModalError on 500 or a missing reply", async () => {
    await expect(modalClient(CFG, fakeFetch(json(500, {})).f).result("fc-1")).rejects.toBeInstanceOf(ModalError);
    await expect(modalClient(CFG, fakeFetch(json(200, {})).f).result("fc-1")).rejects.toBeInstanceOf(ModalError);
  });
});
