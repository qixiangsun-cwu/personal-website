import { DurableObject } from "cloudflare:workers";

// 运营后台 /ws/monitor 的实时事件总线：全局单实例，向所有已连接的监控端广播。
export class MonitorBus extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.ctx = ctx;
    this.env = env;
    this.clients = new Set();
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/ws") {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair);
      server.accept();
      this.clients.add(server);
      server.addEventListener("message", () => {
        /* 原版只 push 不处理上行消息（心跳） */
      });
      const drop = () => this.clients.delete(server);
      server.addEventListener("close", drop);
      server.addEventListener("error", drop);
      return new Response(null, { status: 101, webSocket: client });
    }

    if (url.pathname === "/publish") {
      let event = {};
      try {
        event = await request.json();
      } catch (_e) {
        event = {};
      }
      this._broadcast(event);
      return Response.json({ ok: true, clients: this.clients.size });
    }

    if (url.pathname === "/count") {
      return Response.json({ clients: this.clients.size });
    }

    return new Response("not found", { status: 404 });
  }

  _broadcast(event) {
    const payload = JSON.stringify(event);
    for (const ws of Array.from(this.clients)) {
      try {
        ws.send(payload);
      } catch (_e) {
        this.clients.delete(ws);
      }
    }
  }
}
