import WebSocket from "ws";
import { argv } from 'node:process';


const WS_URL = process.env["WS_URL"];
const CHECK_ID = argv[2];

const ws = new WebSocket(WS_URL);

ws.on("open", function open() {
    console.log(`connected to ${WS_URL}`);
    console.log(`subscribing to ${CHECK_ID}`);
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