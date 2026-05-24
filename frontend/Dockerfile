FROM nginx:alpine

# 删除默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 自定义配置（含反向代理）
COPY nginx.conf /etc/nginx/conf.d/

# 静态文件
COPY index.html /usr/share/nginx/html/
COPY css/ /usr/share/nginx/html/css/
COPY js/ /usr/share/nginx/html/js/
COPY lib/ /usr/share/nginx/html/lib/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
