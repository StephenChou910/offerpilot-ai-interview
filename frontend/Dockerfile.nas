# NAS 部署使用独立的前端镜像：在容器构建时完成 Vite 打包，避免在 NAS 宿主机安装 Node.js。
FROM node:20-alpine AS build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html
# NAS build contexts may retain restrictive file modes; nginx workers need read access.
RUN chmod -R a+rX /usr/share/nginx/html

EXPOSE 80
