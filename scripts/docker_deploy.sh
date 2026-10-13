#!/usr/bin/env bash
# Linux/macOS: bash scripts/docker_deploy.sh [--port 8080] [--stop]
set -Eeuo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
state_dir="$project_root/.runtime/docker"
env_file="$state_dir/compose.env"
secrets_dir="$state_dir/secrets"
project_name='course-registration'
explicit_project=0
port='8080'
explicit_port=0
stop=0
check_only=0
staging_dir=''
lock_owned=0

fail() { printf '部署操作未完成：%s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' '用法：bash scripts/docker_deploy.sh [--port 8080] [--project course-registration] [--stop] [--check-only]'
}
cleanup() {
    if [[ -n "$staging_dir" && "$(dirname -- "$staging_dir")" == "$state_dir" && "$(basename -- "$staging_dir")" == .secrets-* ]]; then
        rm -rf -- "$staging_dir"
    fi
    if [[ "$lock_owned" == 1 ]]; then rmdir -- "$state_dir/.deploy-lock" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'printf "部署命令执行失败（第 %s 行）；已有数据库和密钥均保留。\n" "$LINENO" >&2' ERR

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) [[ $# -ge 2 ]] || fail '--port 后必须提供端口号'; port="$2"; explicit_port=1; shift 2 ;;
        --project) [[ $# -ge 2 ]] || fail '--project 后必须提供项目名'; project_name="$2"; explicit_project=1; shift 2 ;;
        --stop) stop=1; shift ;;
        --check-only) check_only=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage; fail "未知参数：$1" ;;
    esac
done
[[ "$project_name" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail '项目名只允许小写字母、数字、下划线和连字符，首字符必须是字母或数字。'
[[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || fail '端口必须为 1～65535 的整数。'
command -v docker >/dev/null 2>&1 || fail '未检测到 Docker。Linux 请先安装 Docker Engine 和 Compose 插件；macOS 请先安装并启动 Docker Desktop（https://www.docker.com/products/docker-desktop/）。随后重新运行本脚本，项目镜像和依赖将自动下载。'
compose_version="$(docker compose version --short 2>/dev/null)" || fail 'Docker Compose 不可用，请安装或更新 Compose 插件，使 docker compose version 正常执行。'
engine="$(docker info --format '{{.OSType}}' 2>/dev/null)" || fail '无法连接 Docker 引擎。请启动 Docker 并检查当前用户访问权限；可用 docker info 排查。'
[[ "$engine" == linux ]] || fail '需要 Linux 容器引擎，请切换 Docker Desktop 到 Linux containers。'
[[ -f "$project_root/compose.yaml" ]] || fail '缺少项目根目录 compose.yaml。'
if [[ "$check_only" == 1 ]]; then
    printf '检查通过：Linux Docker 引擎和 Compose %s 可用。未生成密钥，也未启动容器。\n' "$compose_version"
    exit 0
fi

if [[ -f "$env_file" && "$explicit_project" == 0 ]]; then
    saved_project="$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$env_file" | tr -d '\r')"
    if [[ -n "$saved_project" ]]; then
        [[ "$saved_project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || fail 'compose.env 的 COMPOSE_PROJECT_NAME 不合法或重复。'
        project_name="$saved_project"
    fi
fi

compose=(docker compose --env-file "$env_file" -p "$project_name" -f "$project_root/compose.yaml")
logs_hint() {
    printf '故障排查：在项目目录执行 docker compose --env-file .runtime/docker/compose.env -p %s -f compose.yaml logs --tail 100 init api mysql catalog billing worker\n' "$project_name" >&2
    printf '修复后重新运行即可；请勿公开密钥文件。\n' >&2
}
if [[ "$stop" == 1 ]]; then
    [[ -f "$env_file" ]] || fail '未找到 .runtime/docker/compose.env，请使用曾经部署该项目的完整项目目录停止服务。'
    "${compose[@]}" down || fail '停止容器失败，请检查 Docker 引擎状态。未删除数据库卷。'
    printf '%s\n' 'Docker 服务已停止。数据库卷、密钥和演示账号文件均已保留，再次运行部署脚本即可启动。'
    exit 0
fi

mkdir -p -- "$state_dir"
chmod 700 "$state_dir"
mkdir -- "$state_dir/.deploy-lock" 2>/dev/null || fail '另一个部署脚本正在运行；请等待它结束。如果上次被强制关闭，请确认无部署进程后删除 .runtime/docker/.deploy-lock 空目录再试。'
lock_owned=1
if [[ -f "$env_file" && "$explicit_port" == 0 ]]; then
    port="$(sed -n 's/^APP_PORT=//p' "$env_file" | tr -d '\r')"
    [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || fail 'compose.env 的 APP_PORT 配置不合法，请用 --port 8080 明确指定端口。'
fi

volume_name="${project_name}_mysql_data"
volume_list="$(docker volume ls --format '{{.Name}}')" || fail '无法读取 Docker 数据卷清单；未生成或替换密钥。'
has_database=0
while IFS= read -r existing_volume; do
    [[ "$existing_volume" != "$volume_name" ]] || has_database=1
done <<< "$volume_list"
secret_names=(mysql_root_password app_db_password catalog_db_password billing_db_password encryption_key integration_key)
if [[ -e "$secrets_dir" ]]; then
    for name in "${secret_names[@]}"; do
        [[ -f "$secrets_dir/$name" ]] || fail "密钥文件缺失：$name。请从备份恢复完整 .runtime/docker/secrets，不能替换已有数据库的凭据。"
        value="$(cat -- "$secrets_dir/$name")"
        if [[ "$name" == encryption_key ]]; then
            [[ "$value" =~ ^[A-Za-z0-9_-]{43}=$ ]] || fail "密钥文件格式不正确：$name。请恢复原文件；密钥内容未输出。"
        else
            [[ "$value" =~ ^[A-Za-z0-9_-]{43}$ ]] || fail "密钥文件格式不正确：$name。请恢复原文件；密钥内容未输出。"
        fi
        unset value
    done
else
    [[ "$has_database" == 0 ]] || fail "已存在数据库卷 $volume_name，但缺少本地密钥。请恢复原 .runtime/docker/secrets；不会生成替代密钥。"
    [[ -r /dev/urandom ]] || fail '系统缺少可读取的 /dev/urandom，无法安全生成随机密钥。'
    command -v base64 >/dev/null 2>&1 || fail '系统缺少 base64 命令，无法生成密钥。'
    staging_dir="$(mktemp -d "$state_dir/.secrets-XXXXXXXX")"
    for name in "${secret_names[@]}"; do
        value="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\r\n')"
        if [[ "$name" != encryption_key ]]; then value="${value%=}"; fi
        printf '%s' "$value" > "$staging_dir/$name"
        unset value
    done
    mv -- "$staging_dir" "$secrets_dir"
    staging_dir=''
    printf '%s\n' '首次部署密钥已随机生成并保存在本地受限目录，未写入源码或终端。'
fi
mkdir -p -- "$state_dir/accounts"
chmod 700 "$secrets_dir" "$state_dir/accounts"
chmod 600 "$secrets_dir/"*
[[ "$state_dir" != *'"'* && "$state_dir" != *$'\r'* && "$state_dir" != *$'\n'* && "$state_dir" != *'\'* ]] || fail '项目路径不能包含双引号、反斜杠或换行，请移动目录后重试。'
escaped_state="${state_dir//\$/\$\$}"
env_temp="$(mktemp "$state_dir/compose-tmp-XXXXXXXX")"
printf 'APP_PORT=%s\nCOMPOSE_PROJECT_NAME=%s\nDOCKER_STATE_DIR="%s"\nLOCAL_UID=%s\nLOCAL_GID=%s\n' "$port" "$project_name" "$escaped_state" "$(id -u)" "$(id -g)" > "$env_temp"
mv -f -- "$env_temp" "$env_file"
"${compose[@]}" config --quiet || fail 'Docker Compose 配置校验失败，请检查上方错误；数据和密钥已保留。'
printf '%s\n' '正在自动下载镜像、安装项目依赖、构建前端并初始化数据库。首次运行可能需要较长时间。'
if ! "${compose[@]}" up --build --detach; then
    logs_hint
    fail '构建或启动失败，请修复网络、端口或初始化错误后重试；数据库和密钥已保留。'
fi

address="http://127.0.0.1:$port"
deadline=$((SECONDS + 300))
ready=0
while (( SECONDS < deadline )); do
    if command -v curl >/dev/null 2>&1; then
        if curl --fail --silent --max-time 5 "$address/api/v1/health" >/dev/null 2>&1; then ready=1; break; fi
    else
        if "${compose[@]}" exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health", timeout=5).read()' >/dev/null 2>&1; then ready=1; break; fi
    fi
    sleep 3
done
if [[ "$ready" == 0 ]]; then logs_hint; fail '等待系统就绪超过 5 分钟。请检查服务日志；未删除任何数据。'; fi
printf '部署成功：%s\n演示账号文件：%s/accounts/演示账号.txt\n' "$address" "$state_dir"
printf '%s\n' '再次运行只会更新和启动服务，不会重置数据库或演示密码。请备份 .runtime/docker 和数据库。'
