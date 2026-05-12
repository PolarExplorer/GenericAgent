@echo off
chcp 65001 >nul
title GenericAgent 启动器

:: 激活 conda agent 环境
call conda activate GenericAgent

:: 进入 GenericAgent 目录（请修改为你实际的路径）
cd /d "D:\AI\GenericAgent"

:: 启动
python launch.pyw --feishu --wecom

pause