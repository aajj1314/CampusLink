#!/bin/sh
# CampusLink Router 启动包装。
#
# 不依赖 procd 的 env 传递（部分改版 OpenWrt 的 procd 对 procd_set_param env
# 不生效，导致 python 找不到 campuslink）。这里自己 export 再 exec，最通用。
#
# 由 /etc/init.d/campuslink 的 procd_set_param command 指向本脚本。
export PYTHONPATH=/opt/campuslink
export CAMPUSLINK_VENDOR=1
export HOME=/tmp/campuslink
exec /usr/bin/python3 -m campuslink.router --host 0.0.0.0 --port 7700
