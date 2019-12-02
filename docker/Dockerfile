FROM ubuntu:18.04

#ARG

#EXPOSE

#ENV

RUN   apt-get update && \
      apt-get install -y \
      git \
      python3 \
      cloc \
      python3-pip

RUN git clone https://github.com/ArtyMandy/kibble-scanners.git /var/www/kibble-scanners

WORKDIR /var/www/kibble-scanners

RUN pip3 install requests elasticsearch certifi python-dateutil pyyaml

CMD bin/sh
