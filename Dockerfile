# container specification to run Same Story checks
FROM docker.io/python:3.9

# install system dependencies
RUN apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-key C99B11DEB97541F0
RUN apt-get -y update
RUN apt-get -yq install software-properties-common
RUN apt-get -y update
RUN apt-get -yq install jq openssh-client imagemagick curl unzip

RUN FILE=gh_2.14.7_linux_$(dpkg --print-architecture).deb && curl -sLO https://github.com/cli/cli/releases/download/v2.14.7/$FILE && dpkg -i $FILE

RUN curl -fsSL https://deb.nodesource.com/setup_16.x | bash -
RUN apt-get install -y nodejs

RUN apt-get install -yq chromium awscli

WORKDIR /code

COPY . .

RUN pip install -r requirements.txt
