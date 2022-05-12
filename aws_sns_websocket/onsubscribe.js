'use strict';

const AWS = require('aws-sdk');

const docClient = new AWS.DynamoDB.DocumentClient({
    apiVersion: '2012-08-10'
});

exports.handler = async (event, context) => {
    console.log(JSON.stringify(event, null, 2));
    console.log(JSON.stringify(context, null, 2));
    const connectionId = event.requestContext.connectionId;

    console.log(`connectionId: ${connectionId}`);

    const {
        TABLE_NAME
    } = process.env;

    var params = {
        TableName: TABLE_NAME,
        Item: {
            'connectionId': connectionId,
        },
        ReturnValues: 'NONE'
    };

    const data = await docClient.put(params).promise();
    return {
        statusCode: 200,
        body: "Subscribed for: " + connectionId
    };
};