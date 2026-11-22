// 用原始 TCP + 手写 WebSocket 握手，确认 Worker 的 /ws/call 是否返回 101 及其 body
// 用法：node ws-probe.mjs <host> <path>
import net from "node:net";
import tls from "node:tls";

const host = process.argv[2];
const path = process.argv[3];

const sock = tls.connect({ host, port: 443, servername: host, family: 4 }, () => {
  const req =
    `GET ${path} HTTP/1.1\r\n` +
    `Host: ${host}\r\n` +
    `Upgrade: websocket\r\n` +
    `Connection: Upgrade\r\n` +
    `Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n` +
    `Sec-WebSocket-Version: 13\r\n` +
    `User-Agent: probe\r\n` +
    `\r\n`;
  sock.write(req);
});

let buf = "";
let done = false;
sock.on("data", (d) => {
  buf += d.toString("utf8");
  if (!done && buf.includes("\r\n\r\n")) {
    done = true;
    console.log("--- response head ---");
    console.log(buf.slice(0, 1200));
    setTimeout(() => {
      sock.destroy();
      process.exit(0);
    }, 1500);
  }
});
sock.on("error", (e) => {
  console.log("SOCKET ERROR", e.message);
  process.exit(1);
});
setTimeout(() => {
  console.log("TIMEOUT");
  console.log(buf.slice(0, 800));
  sock.destroy();
  process.exit(1);
}, 20000);
