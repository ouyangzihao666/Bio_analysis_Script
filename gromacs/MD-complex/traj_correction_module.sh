#!/bin/bash
# GROMACS 轨迹周期性校正模块
# 支持两种调用方式：
# 1. 被主脚本 source 调用：提供 perform_traj_correction 函数
# 2. 独立命令行调用：直接处理单个体系

set -euo pipefail

# ====================== 基础功能函数（如果被独立调用则定义） ======================
if ! declare -f log &> /dev/null; then
    log() {
        local level="$1"
        local message="$2"
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] $level: $message"
    }
fi

if ! declare -f check_and_skip &> /dev/null; then
    check_and_skip() {
        local file="$1"
        local step="$2"
        if [ "${SKIP_EXISTING:-true}" = "true" ] && [ -f "$file" ]; then
            log "INFO" "跳过 $step - 文件已存在: $file"
            return 0
        else
            return 1
        fi
    }
fi

if ! declare -f run_gmx &> /dev/null; then
    run_gmx() {
        local cmd="$1"
        local output_file="$2"
        local step="$3"
        
        log "INFO" "开始执行: $step"
        log "DEBUG" "执行命令: $cmd"
        
        if eval "$cmd"; then
            if [ -n "$output_file" ] && [ ! -f "$output_file" ]; then
                log "WARNING" "命令执行成功但输出文件未找到: $output_file"
                return 1
            fi
            log "INFO" "完成: $step"
            return 0
        else
            log "ERROR" "失败: $step"
            log "ERROR" "失败的命令: $cmd"
            return 1
        fi
    }
fi

# ====================== 核心校正函数（供主脚本调用） ======================
perform_traj_correction() {
    # 输入参数（从主脚本传递）
    local SYSTEM_DIR="$1"
    local SYSTEM_NAME="$2"
    local CENTER_GROUP="${3:-Protein}"
    local OUTPUT_GROUP="${4:-System}"
    local TRAJ_CORRECTION_METHOD="${5:-pbc_mol}"
    local SKIP_EXISTING="${6:-true}"

    # 切换到体系目录
    cd "$SYSTEM_DIR" || exit 1
    log "INFO" "===== 开始轨迹周期性校正: ${SYSTEM_NAME} ====="

    # 定义输入文件（来自步骤10的MD结果）
    local INPUT_TPR="${SYSTEM_DIR}/10_md_production/${SYSTEM_NAME}_md.tpr"
    local INPUT_TRAJ="${SYSTEM_DIR}/10_md_production/${SYSTEM_NAME}_md.xtc"
    local INPUT_GRO="${SYSTEM_DIR}/10_md_production/${SYSTEM_NAME}_md.gro"

    # 前置检查
    if [ ! -f "$INPUT_TPR" ] || [ ! -f "$INPUT_TRAJ" ] || [ ! -f "$INPUT_GRO" ]; then
        log "ERROR" "轨迹校正失败：MD生产模拟结果文件不存在"
        log "ERROR" "请检查: $INPUT_TPR, $INPUT_TRAJ, $INPUT_GRO"
        return 1
    fi

    # 创建校正目录
    local STEP_DIR="${SYSTEM_DIR}/11_traj_correction"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1

    # 定义输出文件
    local CORRECTED_TRAJ="${SYSTEM_NAME}_md_corrected.xtc"
    local CORRECTED_GRO="${SYSTEM_NAME}_md_corrected.gro"
    local REF_FRAME="${SYSTEM_NAME}_first_frame.gro"

    # 跳过已完成的校正
    if ! check_and_skip "$CORRECTED_TRAJ" "轨迹周期性校正"; then

        # 遵循官方推荐工作流：
        # 1. 使分子完整 (Whole)
        # 2. 聚类 (Cluster, 可选)
        # 3. 去跳跃 (NoJump, 需第一帧参考)
        # 4. 中心化 (Center)
        # 5. 盒子调整 (PBC + Unit Cell)
        # 6. 拟合 (Fit, 可选)


        # ---------------- 步骤1：使分子完整 ----------------
        local TRAJ_WHOLE="${SYSTEM_NAME}_temp_whole.xtc"
        if ! check_and_skip "$TRAJ_WHOLE" "步骤1/5：使分子完整"; then
            run_gmx "echo 'System' | gmx trjconv -f '$INPUT_TRAJ' -s '$INPUT_TPR' -o '$TRAJ_WHOLE' -pbc whole" \
                    "$TRAJ_WHOLE" "步骤1/5：使分子完整"
        fi

        # ---------------- 步骤2：提取第一帧作为NoJump参考 ----------------
        if ! check_and_skip "$REF_FRAME" "步骤2/5：提取参考帧"; then
            run_gmx "echo 'System' | gmx trjconv -f '$TRAJ_WHOLE' -s '$INPUT_TPR' -dump 0 -o '$REF_FRAME'" \
                    "$REF_FRAME" "步骤2/5：提取参考帧"
        fi

        # ---------------- 步骤3：去周期性跳跃 ----------------
        local TRAJ_NOJUMP="${SYSTEM_NAME}_temp_nojump.xtc"
        if ! check_and_skip "$TRAJ_NOJUMP" "步骤3/5：去周期性跳跃"; then
            # 关键：使用提取的第一帧作为-s参考，避免撤销整分子操作
            run_gmx "echo 'System' | gmx trjconv -f '$TRAJ_WHOLE' -s '$REF_FRAME' -o '$TRAJ_NOJUMP' -pbc nojump" \
                    "$TRAJ_NOJUMP" "步骤3/5：去周期性跳跃"
        fi

        # ---------------- 步骤4：中心化 + 盒子调整 ----------------
        if [ "$TRAJ_CORRECTION_METHOD" != "pbc_none" ] && [ "$TRAJ_CORRECTION_METHOD" != "pbc_nojump" ]; then
            log "INFO" "步骤4/5：执行 $TRAJ_CORRECTION_METHOD 校正 + 居中"
            local PBC_ARG=""
            case "$TRAJ_CORRECTION_METHOD" in
                "pbc_mol") PBC_ARG="mol" ;;
                "pbc_atom") PBC_ARG="atom" ;;
                "pbc_res") PBC_ARG="res" ;;
                *) log "WARNING" "未知校正方法 $TRAJ_CORRECTION_METHOD，默认使用 pbc_mol"; PBC_ARG="mol" ;;
            esac
            
            # 轨迹校正
            run_gmx "echo -e '$CENTER_GROUP\n$OUTPUT_GROUP' | gmx trjconv -f '$TRAJ_NOJUMP' -s '$INPUT_TPR' -o '$CORRECTED_TRAJ' -center -pbc $PBC_ARG -ur compact" \
                    "$CORRECTED_TRAJ" "步骤4/5：轨迹中心化与PBC校正"
            
            # 同步生成校正后的结构文件
            run_gmx "echo -e '$CENTER_GROUP\n$OUTPUT_GROUP' | gmx trjconv -f '$INPUT_GRO' -s '$INPUT_TPR' -o '$CORRECTED_GRO' -center -pbc $PBC_ARG -ur compact" \
                    "$CORRECTED_GRO" "步骤4/5：结构文件中心化与PBC校正"
        else
            # 仅去跳跃模式
            log "INFO" "步骤4/5：仅保留去跳跃结果，不执行额外PBC校正"
            mv -f "$TRAJ_NOJUMP" "$CORRECTED_TRAJ"
            cp -f "$INPUT_GRO" "$CORRECTED_GRO"
        fi

        # ---------------- 步骤5：清理临时文件 ----------------
        log "INFO" "步骤5/5：清理临时文件"
        rm -f "$TRAJ_WHOLE" "$TRAJ_NOJUMP" "$REF_FRAME"
    fi

    log "INFO" "===== 轨迹周期性校正完成: ${SYSTEM_NAME} ====="
    log "INFO" "最终输出文件："
    log "INFO" "  - 校正轨迹：$STEP_DIR/$CORRECTED_TRAJ"
    log "INFO" "  - 校正结构：$STEP_DIR/$CORRECTED_GRO"
}

# ====================== 独立调用入口（如果直接执行此脚本） ======================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    # 解析命令行参数
    usage() {
        echo "Usage: $0 -d <system_dir> -n <system_name> [-c <center_group>] [-o <output_group>] [-m <method>] [-s <skip_existing>]"
        echo "  -d: 体系根目录（包含10_md_production的目录）"
        echo "  -n: 体系名称（如 TVTLW+YKR_BEA）"
        echo "  -c: 居中组（默认：Protein）"
        echo "  -o: 输出组（默认：System）"
        echo "  -m: 校正方法（pbc_none/pbc_nojump/pbc_mol/pbc_atom，默认：pbc_mol）"
        echo "  -s: 跳过已存在文件（true/false，默认：true）"
        exit 1
    }

    # 默认值
    CENTER_GROUP="Protein"
    OUTPUT_GROUP="System"
    TRAJ_CORRECTION_METHOD="pbc_mol"
    SKIP_EXISTING="true"
    SYSTEM_DIR=""
    SYSTEM_NAME=""

    while getopts "d:n:c:o:m:s:h" opt; do
        case "$opt" in
            d) SYSTEM_DIR=$(realpath "$OPTARG") ;;
            n) SYSTEM_NAME="$OPTARG" ;;
            c) CENTER_GROUP="$OPTARG" ;;
            o) OUTPUT_GROUP="$OPTARG" ;;
            m) TRAJ_CORRECTION_METHOD="$OPTARG" ;;
            s) SKIP_EXISTING="$OPTARG" ;;
            h) usage ;;
            *) usage ;;
        esac
    done

    # 验证必填参数
    if [ -z "$SYSTEM_DIR" ] || [ -z "$SYSTEM_NAME" ]; then
        echo "错误：必须指定 -d（体系目录）和 -n（体系名称）"
        usage
    fi

    # 执行校正
    perform_traj_correction "$SYSTEM_DIR" "$SYSTEM_NAME" "$CENTER_GROUP" "$OUTPUT_GROUP" "$TRAJ_CORRECTION_METHOD" "$SKIP_EXISTING"
fi