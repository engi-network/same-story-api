import { CognitoIdentityClient } from "@aws-sdk/client-cognito-identity";
import { fromCognitoIdentityPool } from "@aws-sdk/credential-provider-cognito-identity";
import { SQSClient, ReceiveMessageCommand, DeleteMessageCommand } from "@aws-sdk/client-sqs";

// Set the AWS Region
const REGION = "us-west-2";

const sqsClient = new SQSClient({
    region: REGION,
    credentials: fromCognitoIdentityPool({
        client: new CognitoIdentityClient({ region: REGION }),
        identityPoolId: process.env["IDENTITY_POOL_ID"]
    }),
});

const queueUrl = process.env["QUEUE_URL"];

const processMsg = async (msg) => {
    const msg_ = JSON.parse(msg.Body);
    console.log("message", msg_.Message);
    if (msg_.Message.check_id === "") {
        // TODO check the check_id before deleting the status message
    }
    var deleteParams = {
        QueueUrl: queueUrl,
        ReceiptHandle: msg.ReceiptHandle
    };
    const data_ = await sqsClient.send(new DeleteMessageCommand(deleteParams));
    console.log("message deleted", data_);
}

const receiveMessages = async () => {
    const params = {
        QueueUrl: queueUrl,
        WaitTimeSeconds: 1,
        MaxNumberOfMessages: 10,
    };
    console.log("checking for messages");
    const data = await sqsClient.send(new ReceiveMessageCommand(params));
    if (data.Messages) {
        await Promise.all(data.Messages.map(processMsg));
    }
}

const run = async () => {
    console.log("IDENTITY_POOL_ID =", process.env["IDENTITY_POOL_ID"]);
    console.log("QUEUE_URL =", queueUrl);
    setInterval(receiveMessages, 10000);
};
run();