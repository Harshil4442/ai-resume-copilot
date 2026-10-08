import { assertFixtureConfiguration, decode64 } from "./protocol.js";

export class FixtureTransport {
  constructor(configuration, identity) { this.configuration = configuration; this.identity = identity; }
  async post(path, body) {
    assertFixtureConfiguration(this.configuration);
    const response = await fetch(`${this.configuration.authorityOrigin}${path}`, { method: "POST", credentials: "omit", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(3_000), headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > 8_000_000) throw new Error("Authority response exceeded the allowed size");
    const result = JSON.parse(new TextDecoder().decode(bytes));
    if (!response.ok) throw new Error(result.error || "Current online authorization is unavailable");
    return result;
  }
  async call(operation, body, claim = null) {
    const challenge = await this.post("/fixture/challenge", { device_id: this.identity.device_id });
    const request = { version: 1, nonce: challenge.nonce, device_id: this.identity.device_id, operation, body, issued_at: Date.now() };
    const proof = await this.identity.sign(request);
    return this.post("/fixture/device-action", { request, signature: proof.signature, claim });
  }
  enroll(grant) { return this.call("enroll", { grant, public_key: this.identity.public_key, key_sha256: this.identity.key_sha256 }); }
  command(applicationId, claim) { return this.call("command", { application_id: applicationId }, claim); }
  async artifact(command, claim) { return decode64((await this.call("artifact", { command_id: command.jti }, claim)).bytes); }
  approve(command, review_digest, claim) { return this.call("approve", { command_id: command.jti, review_digest, allowed_actions: ["fill"] }, claim); }
  authorize(command, field_id, review_digest, claim) { return this.call("authorize", { command_id: command.jti, field_id, review_digest }, claim); }
  begin(permit, claim) { return this.call("begin", { action_id: permit.jti }, claim); }
  complete(begun, claim) { return this.call("complete", { action_id: begun.action_id }, claim); }
  cancel(command, claim) { return this.call("cancel", { command_id: command.jti }, claim); }
}
