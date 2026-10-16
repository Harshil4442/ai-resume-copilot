// Owned local production-build TLS listener for actual NextAuth secure cookies.
import fs from "node:fs";
import https from "node:https";
import next from "next";
const port = Number(process.env.PORT);
const app = next({ dev: false, hostname: "127.0.0.1", port });
await app.prepare();
https.createServer({ key: fs.readFileSync(process.env.HIREWIZ_TEST_TLS_KEY),
  cert: fs.readFileSync(process.env.HIREWIZ_TEST_TLS_CERT) }, app.getRequestHandler()).listen(port, "127.0.0.1");
