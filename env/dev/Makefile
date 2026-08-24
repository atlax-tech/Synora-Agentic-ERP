# Synora Phase 1 开发环境（Inc-1 / P1.1）
SHELL := /bin/bash
SCRIPT := env/dev/scripts/dev/env.sh

.PHONY: up down reset resolve bootstrap start bash info

up:       ## 启动依赖服务并等待健康
	@$(SCRIPT) up

down:     ## 停止（不删卷）
	@$(SCRIPT) down

reset:    ## 销毁本项目容器与具名卷（危险：需删除确认及卷外备份确认）
	@$(SCRIPT) reset

resolve:  ## 解析 version-16 候选 SHA 到 versions.env
	@$(SCRIPT) resolve

bootstrap:## 解析 SHA+构建+init+建 site+安装 erpnext+断言
	@$(SCRIPT) bootstrap

start:     ## 前台启动 Bench 开发进程
	@$(SCRIPT) start

bash:     ## 在 bench 容器执行命令: make bash CMD="bench --version"
	@$(SCRIPT) bash "$(CMD)"

info:     ## 打印非敏感环境变量键名
	@$(SCRIPT) info
