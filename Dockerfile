# container specification to run Same Story checks
FROM docker.io/python:3.9

# install system dependencies
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-key C99B11DEB97541F0
RUN apt-get -y update
RUN apt-get -yq install software-properties-common
RUN apt-get -y update
RUN apt-add-repository https://cli.github.com/packages
RUN apt-get -y update
RUN apt-get -yq install jq openssh-client gh imagemagick curl unzip 

RUN curl -fsSL https://deb.nodesource.com/setup_16.x | bash -
RUN apt-get install -y nodejs

RUN apt-get install -yq chromium awscli

WORKDIR /code

COPY . .

RUN pip install -r requirements.txt