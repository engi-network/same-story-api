import WebSocket from "ws";
import { argv } from 'node:process';


const WS_URL = process.env["WS_URL"];
const CHECK_ID = argv[2];

var retries = 5;

function connect() {
    var ws;
    if (--retries == 0) {
        console.log(`giving up on ${WS_URL}`);
        process.exit(1);
    }
    console.log(`connecting to ${WS_URL}`);
    ws = new WebSocket(WS_URL);

    ws.on("open", function open() {
        console.log(`subscribing to ${CHECK_ID}`);
        ws.send(JSON.stringify({
            message: "subscribe",
            check_id: CHECK_ID
        }));
        // ws.close();
    });

    ws.on("close", function close() {
        console.log("disconnected");
        connect();
    });

    ws.on("message", function message(data) {
        let message = JSON.parse(data);
        console.log("received: %s", message);
    });
}

connect();
