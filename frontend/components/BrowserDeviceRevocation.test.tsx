import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import BrowserDeviceRevocation from "./BrowserDeviceRevocation";

const device = "00000000-0000-4000-8000-000000000001";
const challenge = { challenge_id:"00000000-0000-4000-8000-000000000002", nonce:"00000000-0000-4000-8000-000000000003",
  operation:"revoke_device", device_id:device, expires_at_ms:Date.now()+60000 };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
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
