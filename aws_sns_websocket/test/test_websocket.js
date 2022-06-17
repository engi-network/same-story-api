import WebSocket from "ws";
import { argv } from 'node:process';


import pino from "pino";
const logger = pino();


const WS_URL = process.env["WS_URL"];
const CHECK_ID = argv[2];

var retries = 5;

function connect() {
    var ws;
    var intervalId;

    if (--retries == 0) {
        logger.info(`giving up on ${WS_URL}`);
        process.exit(1);
    }
    logger.info(`connecting to ${WS_URL}`);
    ws = new WebSocket(WS_URL);

    ws.on("open", function open() {
        logger.info(`subscribing to ${CHECK_ID}`);
        ws.send(JSON.stringify({
            message: "subscribe",
            check_id: CHECK_ID
        }));

        intervalId = setInterval(() => {
            logger.info("ping");
            ws.send(JSON.stringify({ message: "ping" }));
        }, 10000);
    });

    ws.on("close", function close() {
        logger.info("disconnected");
        clearInterval(intervalId);
        connect();
    });

    ws.on("message", function message(data) {
        let message = JSON.parse(data);
        logger.info("received: %s", JSON.stringify(message, null, 2));
    });
}

connect();
