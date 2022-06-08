"use strict";

const AWS = require("aws-sdk");

const docClient = new AWS.DynamoDB.DocumentClient({
    apiVersion: "2012-08-10"
});

exports.handler = async (event, context) => {
    console.log(JSON.stringify(event, null, 2));
    console.log(JSON.stringify(context, null, 2));

    const {
        TABLE_NAME,
        CALL_BACK_URL
    } = process.env;

    let message = JSON.parse(event.Records[0].Sns.Message);
    console.log(JSON.stringify(message, null, 2));

    console.log(`TABLE_NAME: ${TABLE_NAME}`);
    console.log(`CALL_BACK_URL: ${CALL_BACK_URL}`);

    const checkId = message.check_id;

    let connectionData = await docClient.scan({
        TableName: TABLE_NAME,
        ProjectionExpression: "connectionId",
        FilterExpression: "checkId = :checkId",
        ExpressionAttributeValues: {
            ":checkId": checkId
        }
    }).promise();

    const apigwManagementApi = new AWS.ApiGatewayManagementApi({
        apiVersion: "2018-11-29",
        endpoint: CALL_BACK_URL
    });

    const msg = " sending status update via WebSocket";
    const postCalls = connectionData.Items.map(async ({
        connectionId
    }) => {
        apigwManagementApi.postToConnection({
            ConnectionId: connectionId,
            Data: event.Records[0].Sns.Message
        }, function (err, data) {
            if (err) { // an error occurred
                console.log(`error ${msg}`)
                console.log(err, err.stack);
            }
            else { // successful response
                console.log(`success ${msg}`)
            }
        });
    });

    await Promise.all(postCalls);

    return {
        statusCode: 200,
        body: "Message notified"
    };
};
