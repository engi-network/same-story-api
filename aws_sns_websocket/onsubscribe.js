"use strict";

const AWS = require("aws-sdk");

const docClient = new AWS.DynamoDB.DocumentClient({
    apiVersion: "2012-08-10"
});

exports.handler = async (event, context) => {
    console.log(JSON.stringify(event, null, 2));
    console.log(JSON.stringify(context, null, 2));
    const connectionId = event.requestContext.connectionId;
    const checkId = JSON.parse(event.body).check_id;

    console.log(`connectionId: ${connectionId}`);
    console.log(`checkId: ${checkId}`);

    const {
        TABLE_NAME
    } = process.env;

    var params = {
        TableName: TABLE_NAME,
        Item: {
            "connectionId": connectionId,
            "checkId": checkId,
            "ttl": (Date.now() / 1000) + (60 * 30),
        },
        ReturnValues: "NONE"
    };

    const data = await docClient.put(params).promise();
    return {
        statusCode: 200,
        body: "subscribed for: " + connectionId
    };
};