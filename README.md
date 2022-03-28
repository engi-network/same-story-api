## A backend server for processing jobs for Same Story?

### Install

`pipenv install`

### Environment

You'll need AWS credentials, see below.

Create a `.env` file:
```
# a place for the server to checkout and run code
TMPDIR=/tmp

AWS_ACCESS_KEY_ID=""
AWS_SECRET_ACCESS_KEY=""
AWS_DEFAULT_REGION="us-west-2"

# GitHub personal access token
GITHUB_TOKEN=""

# where the server dequeues jobs from SQS
QUEUE_URL="https://us-west-2.queue.amazonaws.com/163803973373/same-story-check-queue"
# where the test code queues jobs 
TOPIC_ARN="arn:aws:sns:us-west-2:163803973373:same-story-check-topic"
```

The GitHub personal access token must grant access to `repository` named in
`specification.json`. See below.

### Setup

If you're starting from scratch with a new AWS account, you'll need to create
the topic and queue then connect the two together with appropriate permissions.
The code to do that is in `nbs/SNS-SQS.ipynb`.

### Docker

When running Docker on Apple silicon:

```
docker buildx create --name mybuilder --platform linux/arm64
docker buildx use mybuilder
docker buildx inspect --bootstrap
docker buildx build -t same-story-api:latest --platform linux/arm64 --load .
```

Run an interactive shell inside the container:

`docker run -i -t same-story-api:latest /bin/bash`

Compose:

`docker-compose up`

The server can only process one job at a time because storycap doesn't seem to
like multiple simultaneous jobs even when a different port is used. 

To process more than one job concurrently, run a bunch of Docker containers like
this:

```
docker compose up -d --scale worker=3
```

And watch the logs:

```
docker-compose logs -f -t
```

### Run outside Docker

```
pipenv run python server/app.py
```

### Run the tests

```
pipenv run pytest -v 
```

### Submitting jobs and getting the results

Have a look at the test code, especially the function `get_results` in `test_same_story_server.py`.

### Submit a new job using ES6 and the AWS SDK for JavaScript

Snippet adapted from the
[aws-doc-sdk-examples](https://github.com/awsdocs/aws-doc-sdk-examples/blob/main/javascript/example_code/sns/sns_publishtotopic.js).

Note this is test code for use in Node. For JavaScript running in a client web
browser or the Figma plugin, use the [Amazon Cognito Identity
service](https://docs.aws.amazon.com/AWSJavaScriptSDK/latest/AWS/CognitoIdentityCredentials.html).

```
// libs/snsClient.js
import { SNSClient } from "@aws-sdk/client-sns";
// Set the AWS Region.
const REGION = "REGION"; //e.g. "us-east-1"
// Create SNS service object.
const snsClient = new SNSClient(/*{ region: REGION }*/);
export { snsClient };
```

```
// sns_publishtotopic.js
import "dotenv/config"

import { PublishCommand } from "@aws-sdk/client-sns";
import { snsClient } from "./libs/snsClient.js";

console.log(`AWS_ACCESS_KEY_ID: ${process.env.AWS_ACCESS_KEY_ID}`)

const d = {
  'check_id': 1644302997171,
}

// Set the parameters
var params = {
  Message: JSON.stringify(d),
  TopicArn: "arn:aws:sns:us-west-2:163803973373:same-story-check-topic",
};

const run = async () => {
  try {
    const data = await snsClient.send(new PublishCommand(params));
    console.log("Success.", data);
    return data; // For unit tests.
  } catch (err) {
    console.log("Error", err.stack);
  }
};
run();
```