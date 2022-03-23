## A backend server for processing jobs for Same Story?

`pipenv install`

### Environment

You'll need the AWS credentials, see below

Create a `.env` file:
```
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION="us-west-2"
```

#### Docker

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

#### Locally

### Run the tests

### Submit a new job using ES6 and the AWS SDK for JavaScript

Snippet adapted from the [aws-doc-sdk-examples](https://github.com/awsdocs/aws-doc-sdk-examples/blob/main/javascript/example_code/sns/sns_publishtotopic.js).

```
import "dotenv/config"

import { PublishCommand } from "@aws-sdk/client-sns";
import { snsClient } from "./libs/snsClient.js";

console.log(`AWS_ACCESS_KEY_ID: ${process.env.AWS_ACCESS_KEY_ID}`)

const d = {
  'check_id': 1644302997171,
  /*
  'width': '800',
  'height': '600',
  'component': 'Button',
  'story': 'Primary',
  'repository': 'engi-network/engi-ui'
  */
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