import WebSocket from "ws";

const ws = new WebSocket("wss://aawv1ibk24.execute-api.us-west-2.amazonaws.com/prod");

ws.on("open", function open() {
    console.log("connected");
    ws.send(JSON.stringify({
        message: "subscribe",
        check_id: "2e2883d3-8d9a-445a-80bc-96a6a99cb3e7"
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