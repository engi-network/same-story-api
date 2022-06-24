import { CognitoIdentityClient } from "@aws-sdk/client-cognito-identity";
import { fromCognitoIdentityPool } from "@aws-sdk/credential-provider-cognito-identity";
import { SQSClient, ReceiveMessageCommand, DeleteMessageCommand, GetQueueUrlCommand } from "@aws-sdk/client-sqs";
import { argv } from "node:process";
import pino from "pino";

const log = pino();


// AWS Region
const REGION = "us-west-2";
const IDENTITY_POOL_ID = process.env["IDENTITY_POOL_ID"];
const ENV = process.env["ENV"] || "dev"; // also "staging" and "production"
const CHECK_ID = argv[2];

const sqsClient = new SQSClient({
    region: REGION,
    credentials: fromCognitoIdentityPool({
        client: new CognitoIdentityClient({ region: REGION }),
        identityPoolId: IDENTITY_POOL_ID
    }),
});

const getSQSUrl = async () => {
    const name = `same-story-api-${ENV}-${CHECK_ID}-status.fifo`;
    log.info(`getting SQS queue: ${name}`);
    const data = await sqsClient.send(new GetQueueUrlCommand({ QueueName: name }));
    return data["QueueUrl"]
}

const QUEUE_URL = await getSQSUrl();

const stringify = (thing) => {
    return JSON.stringify(thing, null, 2);
}

const processMsg = async (msg) => {
    const status_msg = JSON.parse(JSON.parse(msg.Body).Message);
    log.info(`status_msg: ${stringify(status_msg)}`);
    await sqsClient.send(new DeleteMessageCommand({
        QueueUrl: QUEUE_URL,
        ReceiptHandle: msg.ReceiptHandle
    }));
    const { step, step_count, error } = status_msg;
    if ((step === step_count - 1) || (error != null)) {
        process.exit(0);
    }
}

const receiveMessages = async () => {
    log.info("checking for messages");
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
    log.info(`IDENTITY_POOL_ID='${IDENTITY_POOL_ID}'`);
    log.info(`QUEUE_URL='${QUEUE_URL}'`);
    setInterval(receiveMessages, 5000);
};

run();