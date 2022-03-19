# container specification to run Same Story checks

#FROM regviz/node-xcb
FROM nikolaik/python-nodejs:python3.9-nodejs17

WORKDIR /code

COPY . .

# install system dependencies
RUN apt-get -yq install gnupg
RUN apt-get -y update
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-key C99B11DEB97541F0
RUN apt-get -y update
RUN apt-get -yq install software-properties-common
RUN apt-get -y update
RUN apt-add-repository https://cli.github.com/packages
RUN apt-get -y update
RUN apt-get -yq install jq openssh-client gh imagemagick curl unzip

# storycap deps
RUN apt-get install -yq gconf-service libasound2 libatk1.0-0 libc6 libcairo2 libcups2 libdbus-1-3 \
    libexpat1 libfontconfig1 libgcc1 libgconf-2-4 libgdk-pixbuf2.0-0 libglib2.0-0 libgtk-3-0 libnspr4 \
    libpango-1.0-0 libpangocairo-1.0-0 libstdc++6 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
    libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 libxtst6 \
    ca-certificates fonts-liberation libappindicator1 libnss3 lsb-release xdg-utils wget

RUN python3 -m pip install -r requirements.txt

# install aws cli
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
RUN unzip awscliv2.zip
RUN ./aws/install

RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add -
RUN sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list'
RUN apt-get update && apt-get install -y google-chrome-stable
