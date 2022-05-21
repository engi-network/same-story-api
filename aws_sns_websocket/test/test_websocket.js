import WebSocket from "ws";

const WS_URL = process.env["WS_URL"];
const CHECK_ID = process.env["CHECK_ID"];

const ws = new WebSocket(WS_URL);

ws.on("open", function open() {
    console.log("connected");
    ws.send(JSON.stringify({
        message: "subscribe",
        check_id: CHECK_ID
    }));
    // ws.close();
});

ws.on("close", function close() {
    console.log("disconnected");
});

ws.on("message", function message(data) {
    let message = JSON.parse(data);
    console.log("received: %s", message);
});