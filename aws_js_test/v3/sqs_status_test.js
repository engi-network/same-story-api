import { CognitoIdentityClient } from "@aws-sdk/client-cognito-identity";
import { fromCognitoIdentityPool } from "@aws-sdk/credential-provider-cognito-identity";
import { SQSClient, ReceiveMessageCommand, DeleteMessageCommand } from "@aws-sdk/client-sqs";
import pino from "pino";

const logger = pino();

// be careful with this script b/c it deletes status messages from QUEUE_URL w/o
// filtering on check_id
// could cause a lot of confusion if left running on a laptop somewhere :)

// AWS Region
const REGION = "us-west-2";
const QUEUE_URL = process.env["QUEUE_URL"];
const IDENTITY_POOL_ID = process.env["IDENTITY_POOL_ID"];

const sqsClient = new SQSClient({
    region: REGION,
    credentials: fromCognitoIdentityPool({
        client: new CognitoIdentityClient({ region: REGION }),
        identityPoolId: IDENTITY_POOL_ID
    }),
});


const stringify = (thing) => {
    return JSON.stringify(thing, null, 2);
}

const processMsg = async (msg) => {
    const msg_ = JSON.parse(msg.Body);
    logger.info(`message: ${msg_.Message}`);
    if (msg_.Message.check_id === "") {
        // TODO check the check_id before deleting the status message
    }
    const data_ = await sqsClient.send(new DeleteMessageCommand({
        QueueUrl: QUEUE_URL,
        ReceiptHandle: msg.ReceiptHandle
    }));
    logger.info(`message deleted: ${stringify(data_)}`);
}

const receiveMessages = async () => {
    logger.info("checking for messages");
    const data = await sqsClient.send(new ReceiveMessageCommand({
        QueueUrl: QUEUE_URL,
        WaitTimeSeconds: 1,
        MaxNumberOfMessages: 10,
    }));
    if (data.Messages) {
        await Promise.all(data.Messages.map(processMsg));
    }
}

const run = async () => {
    logger.info(`IDENTITY_POOL_ID='${IDENTITY_POOL_ID}'`);
    logger.info(`QUEUE_URL='${QUEUE_URL}'`);
    setInterval(receiveMessages, 10000);
};

run();