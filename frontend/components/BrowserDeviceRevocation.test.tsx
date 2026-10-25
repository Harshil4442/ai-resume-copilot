import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import BrowserDeviceRevocation from "./BrowserDeviceRevocation";

const device = "00000000-0000-4000-8000-000000000001";
const challenge = { challenge_id:"00000000-0000-4000-8000-000000000002", nonce:"00000000-0000-4000-8000-000000000003",
  operation:"revoke_device", device_id:device, expires_at_ms:Date.now()+60000 };
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function timedRevocation(milliseconds: number | null) {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, "timeout").mockImplementation((delay) => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("synthetic-private-timeout", "TimeoutError")), delay);
    return controller.signal;
  });
  const request = vi.fn().mockResolvedValueOnce(Response.json({ csrf_token: "a".repeat(64) }))
    .mockResolvedValueOnce(Response.json({ ...challenge, expires_at_ms: Date.now() + 60_000 }))
    .mockImplementationOnce((_input: unknown, options: RequestInit) => new Promise<Response>((resolve, reject) => {
      const signal = options.signal;
      if (!signal) throw new Error("Expected a bounded revocation request");
      let timer: ReturnType<typeof setTimeout> | undefined;
      const abort = () => { clearTimeout(timer); reject(signal.reason); };
      if (signal.aborted) { abort(); return; }
      signal.addEventListener("abort", abort, { once: true });
      if (milliseconds !== null) timer = setTimeout(() => {
        signal.removeEventListener("abort", abort);
        resolve(Response.json({ status: "REVOKED", device_id: device, event_sequence: 9 }));
      }, milliseconds);
    }));
  vi.stubGlobal("fetch", request);
  render(<BrowserDeviceRevocation deviceId={device} />);
  return request;
}

async function submitTimedRevocation() {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Review disconnection" })); });
  fireEvent.change(screen.getByLabelText("HireWiz password"), { target: { value: "synthetic-revoke-password" } });
  fireEvent.click(screen.getByRole("checkbox"));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Disconnect this browser" })); });
}
function setup(last: Promise<Response> | Response, changed = {}) {
  const request=vi.fn().mockResolvedValueOnce(Response.json({csrf_token:"a".repeat(64)}))
    .mockResolvedValueOnce(Response.json({...challenge,...changed})).mockImplementationOnce(()=>last);
  vi.stubGlobal("fetch",request); render(<BrowserDeviceRevocation deviceId={device} />); return request;
}
async function approve() {
  await userEvent.click(screen.getByRole("button",{name:"Review disconnection"}));
  const submit=await screen.findByRole("button",{name:"Disconnect this browser"});
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText("HireWiz password"),{target:{value:"synthetic-revoke-password"}});
  expect(submit).toBeDisabled(); await userEvent.click(screen.getByRole("checkbox")); await userEvent.click(submit);
}
it("shows native revocation arriving after the former fifteen-second client deadline", async () => {
  const request = timedRevocation(16_000);
  await submitTimedRevocation();
  await act(async () => { await vi.advanceTimersByTimeAsync(15_001); });
  expect(screen.getByRole("button", { name: "Disconnect this browser" })).toBeDisabled();
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(999); });
  expect(screen.getByRole("status")).toHaveTextContent("This browser is disconnected");
  expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  expect(request).toHaveBeenCalledTimes(3);
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(request).toHaveBeenCalledTimes(3);
});
it("locks revocation uncertainty at forty seconds without replay or private error disclosure", async () => {
  const request = timedRevocation(null);
  await submitTimedRevocation();
  await act(async () => { await vi.advanceTimersByTimeAsync(39_999); });
  expect(screen.queryByRole("alert")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(request.mock.calls[2][1].signal.aborted).toBe(true);
  expect(screen.getByRole("alert")).toHaveTextContent("Do not resend");
  expect(screen.getByRole("alert")).not.toHaveTextContent("synthetic-private-timeout");
  expect(screen.getByRole("button", { name: "Review disconnection" })).toBeDisabled();
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByLabelText("HireWiz password")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(request).toHaveBeenCalledTimes(3);
});
it("does no native request before an explicit valid disconnection review",()=>{
  const request=vi.fn();vi.stubGlobal("fetch",request);render(<BrowserDeviceRevocation deviceId={null}/>);
  expect(screen.getByRole("button")).toBeDisabled();expect(request).not.toHaveBeenCalled();
});
it("requires exact device, explicit intent and current password before one revoke",async()=>{
  const request=setup(Response.json({status:"REVOKED",device_id:device,event_sequence:9}));
  expect(request).not.toHaveBeenCalled();await approve();
  expect(await screen.findByRole("status")).toHaveTextContent("disconnected");
  expect(request).toHaveBeenCalledTimes(3);
  expect(request.mock.calls[2][0]).toBe("/api/browser-pairing/candidate/revoke");
  expect(JSON.parse(request.mock.calls[2][1].body)).toMatchObject({operation:"revoke_device",challenge_id:challenge.challenge_id,confirmed:true});
  expect(screen.queryByLabelText("HireWiz password")).toBeNull();
});
it("withholds a changed device challenge without sending password",async()=>{
  const request=setup(Response.json({}),{device_id:"00000000-0000-4000-8000-000000000009"});
  await userEvent.click(screen.getByRole("button",{name:"Review disconnection"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");expect(request).toHaveBeenCalledTimes(2);
  expect(screen.queryByLabelText("HireWiz password")).toBeNull();
});
it.each(["lost","changed_ack"])("locks an uncertain %s outcome without replay or false success",async(kind)=>{
  const response=kind==="lost"?Response.json({detail:"untrusted-private"},{status:503}):Response.json({status:"REVOKED",device_id:"00000000-0000-4000-8000-000000000009",event_sequence:9});
  const request=setup(response);await approve();expect(await screen.findByRole("alert")).toHaveTextContent("Do not resend");
  expect(screen.getByRole("alert")).not.toHaveTextContent("untrusted-private");
  expect(screen.getByRole("button",{name:"Review disconnection"})).toBeDisabled();expect(request).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("status")).toBeNull();
});
