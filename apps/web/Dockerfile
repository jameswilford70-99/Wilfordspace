FROM nginx:1.27-alpine

COPY index.html /usr/share/nginx/html/index.html
COPY calendar-ui.js /usr/share/nginx/html/calendar-ui.js
COPY household-ui.js /usr/share/nginx/html/household-ui.js
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

