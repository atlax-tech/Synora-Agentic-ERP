"use strict";

const http = require("node:http");

const PORT = 18081;
const MAX_BODY_BYTES = 16 * 1024;
const JSON_HEADERS = { "content-type": "application/json" };

function reply(response, statusCode, payload) {
  response.writeHead(statusCode, JSON_HEADERS);
  response.end(JSON.stringify(payload));
}

const server = http.createServer((request, response) => {
  if (request.method !== "POST" || request.url !== "/recorded-gateway") {
    request.resume();
    reply(response, 404, { ok: false, error: "not_found" });
    return;
  }

  let body = "";
  let rejected = false;
  request.setEncoding("utf8");
  request.on("data", (chunk) => {
    if (rejected) return;
    body += chunk;
    if (Buffer.byteLength(body, "utf8") > MAX_BODY_BYTES) {
      rejected = true;
      reply(response, 413, { ok: false, error: "body_too_large" });
      request.destroy();
    }
  });
  request.on("end", () => {
    if (rejected) return;
    let input;
    try {
      input = JSON.parse(body);
    } catch {
      reply(response, 400, { ok: false, error: "invalid_json" });
      return;
    }
    if (
      input.mode !== "PLAN_EXECUTE" ||
      input.tool !== "material_request.open" ||
      typeof input.goal !== "string"
    ) {
      reply(response, 400, { ok: false, error: "recorded_request_rejected" });
      return;
    }
    reply(response, 200, {
      ok: true,
      tool: "material_request.open",
      mode: "PLAN_EXECUTE",
      observation_digest: "recorded-p5-gateway-v1",
    });
  });
});

server.listen(PORT, "127.0.0.1", () => {
  process.stdout.write(`recorded Gateway listening on 127.0.0.1:${PORT}\n`);
});
