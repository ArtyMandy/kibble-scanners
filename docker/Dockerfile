FROM ubuntu:18.04

#ARG

#EXPOSE

#ENV

RUN   apt-get update && \
      apt-get install -y \
      git \
      python3 \
      python3-pip

RUN git clone https://github.com/ArtyMandy/kibble.git /var/www/kibble-scanner

RUN pip3 install requests

CMD bin/sh
