ARG ARCH=arm64v8

FROM ${ARCH}/nginx:latest

RUN apt-get update
# apache2-utils needed for setting nginx creds
RUN apt-get install -y apache2-utils

# Remove default sites
RUN rm -rf /usr/share/nginx/html/*

# Add configuration file
COPY nginx.conf /etc/nginx/nginx.conf
COPY http.conf /etc/nginx/conf.d/default.conf

COPY start.sh /tmp/start.sh
RUN chmod +x /tmp/start.sh

# Start the container
EXPOSE 80

CMD ["/tmp/start.sh"]
