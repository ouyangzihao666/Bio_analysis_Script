#!/bin/bash

# GROMACS分子动力学模拟批处理脚本
# 作者：欧阳梓豪 & TraeCN & 豆包-专家模式
# 版本: 1.0.1
# 使用方法: ./run_md_pipeline.sh <config_file>

# 错误处理设置
# ====================== 自动错误捕获功能（新增）======================
# 启用严格模式 + 错误追踪
set -euo pipefail
set -o errtrace  # 让陷阱在函数内也生效
set -o functrace # 追踪函数错误

# 错误处理函数：出错自动打印行号、命令、位置
error_handler() {
    local ERR_CODE=$?
    local ERR_LINE=$1
    local ERR_CMD=$2
    echo -e "\n==================== 脚本运行失败 ===================="
    echo -e "❌ 错误位置：脚本第 ${ERR_LINE} 行"
    echo -e "❌ 出错命令：${ERR_CMD}"
    echo -e "❌ 错误码：${ERR_CODE}"
    echo -e "📂 当前目录：$(pwd)"
    echo -e "=====================================================\n"
    exit $ERR_CODE
}

# 绑定错误捕获（自动触发）
trap 'error_handler $LINENO "$BASH_COMMAND"' ERR
# =================================================================

# ========== 功能函数定义 ==========

# 日志功能
setup_logging() {
    LOG_FILE="${WORK_DIR:-${PWD}}/md_pipeline_$(date +%Y%m%d_%H%M%S).log"
    touch "$LOG_FILE"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: 日志文件已创建: $LOG_FILE" | tee -a "$LOG_FILE"
}

log() {
    local level="$1"
    local message="$2"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $level: $message" | tee -a "$LOG_FILE"
}

# 显示帮助信息
show_help() {
    echo "Usage: $0 <config_file>"
    echo "GROMACS分子动力学模拟批处理脚本"
    echo "用于自动执行GROMACS MD模拟的完整流程，包括准备、平衡和生产模拟"
    echo ""
    echo "Options:"
    echo "  -h, --help     显示此帮助信息"
    echo "  -v, --version  显示脚本版本"
    echo ""
    echo "示例:"
    echo "  $0 config.conf         # 使用指定配置文件运行"
    echo "  $0 -h                  # 显示帮助信息"
    echo "  $0 -v                  # 显示版本信息"
    echo ""
    echo "配置文件要求:"
    echo "  配置文件包含模拟所需的所有参数，如蛋白质列表、力场、水模型等"
    echo "  可复制config_template.txt并根据需要修改"
}

# 检查GROMACS环境
check_gromacs_env() {
    # 检查gmx命令是否存在
    if ! command -v gmx &> /dev/null; then
        echo "错误：未找到gmx命令，请先加载GROMACS环境！"
        exit 1
    fi
    
    # 兼容Ubuntu的版本提取
    GMX_VERSION=$(gmx --version | grep -oE 'GROMACS version:     [0-9]+\.[0-9]+' | awk '{print $4}')
    echo "检测到GROMACS版本：$GMX_VERSION"
}

# 检查参数
parse_arguments() {
    VERSION="1.0.0"
    CONFIG_FILE=""
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_help
                exit 0
                ;;
            -v|--version)
                echo "GROMACS MD Pipeline Script v$VERSION"
                exit 0
                ;;
            *)
                if [ -z "$CONFIG_FILE" ]; then
                    CONFIG_FILE="$1"
                else
                    echo "错误: 只能指定一个配置文件"
                    show_help
                    exit 1
                fi
                ;;
        esac
        shift
    done
    
        
    echo "使用配置文件: $CONFIG_FILE"
}

# 加载轨迹校正函数
source_traj_module() {
    SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
    TRAJ_CORRECTION_MODULE="$SCRIPT_DIR/../tools/traj_correction_module.sh"
    if [ -f "$TRAJ_CORRECTION_MODULE" ]; then
        source "$TRAJ_CORRECTION_MODULE"
        log "INFO" "已加载轨迹校正模块: $TRAJ_CORRECTION_MODULE"
    else
        log "WARNING" "未找到轨迹校正模块: $TRAJ_CORRECTION_MODULE ，跳过轨迹校正"
        PERFORM_TRAJ_CORRECTION="false"
    fi
}

# ========== 初始化 ==========

# 检查GROMACS环境
check_gromacs_env

# 解析命令行参数
parse_arguments "$@"

# 获取当前用户路径
USER_CWD="$PWD"
#定义日志文件
LOG_FILE="${USER_CWD}/md_pipeline_$(date +%Y%m%d_%H%M%S).log"
touch "$LOG_FILE"

echo "使用配置文件: $CONFIG_FILE"

# ========== 首先设置所有默认值 ==========
SKIP_EXISTING="true"
EM_VERBOSE="true"
EQUIL_VERBOSE="true"
SOLVENT_FILE="spc216.gro"
#默认生理pH值 7.4
PH_VALUE="7.4"
#默认最大警告数 5（配置文件未设置/注释时自动使用）
MAXWARN="5"

# 轨迹校正相关参数的默认值
PERFORM_TRAJ_CORRECTION="true"
TRAJ_CORRECTION_METHOD="pbc_mol"
CENTER_GROUP="Protein"
OUTPUT_GROUP="System"
CORRECTED_TRAJ_SUFFIX="_md_corrected.xtc"

# 加载配置文件
log "INFO" "开始加载配置文件"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "错误：必须指定有效的配置文件"
    exit 1
fi
source "$CONFIG_FILE"

# 重新初始化日志，使用配置文件中的工作目录
setup_logging

if [ "$PERFORM_TRAJ_CORRECTION" = "true" ]; then
    source_traj_module
fi

# ========== 参数验证函数 ==========

# 检查必要参数是否存在
check_required_param() {
    local param_name="$1"
    if [ -z "${!param_name}" ]; then
        log "ERROR" "配置文件中缺少必要参数: $param_name"
        exit 1
    fi
    log "INFO" "参数 $param_name 已设置: ${!param_name}"
}

# 检查参数是否为正数
check_positive_number() {
    local param_name="$1"
    local param_value="${!1}"
    if ! [[ "$param_value" =~ ^[0-9]+([.][0-9]+)?$ ]] || (( $(echo "$param_value <= 0" | bc -l) )); then
        log "ERROR" "参数 $param_name 必须是正数，当前值: $param_value"
        exit 1
    fi
    log "INFO" "参数 $param_name 验证通过: $param_value"
}

# 检查文件是否存在
check_file_exists() {
    local param_name="$1"
    local param_value="${!1}"
    if [ ! -f "$param_value" ]; then
        log "ERROR" "参数 $param_name 指定的文件不存在: $param_value"
        exit 1
    fi
    log "INFO" "文件参数 $param_name 验证通过: $param_value"
}

# 检查目录是否存在
check_dir_exists() {
    local param_name="$1"
    local param_value="${!1}"
    if [ ! -d "$param_value" ]; then
        log "ERROR" "参数 $param_name 指定的目录不存在: $param_value"
        exit 1
    fi
    log "INFO" "目录参数 $param_name 验证通过: $param_value"
}

# 执行必要参数检查
log "INFO" "开始验证必要参数"
required_params=("PROTEIN_LIST" "WORK_DIR" "MDP_DIR" "WATER_MODEL" "FORCE_FIELD" "BOX_TYPE" "BOX_DISTANCE" "ION_CONCENTRATION")
for param in "${required_params[@]}"; do
    check_required_param "$param"
done

# === 路径处理函数 ===
# 将相对路径转换为基于工作目录的绝对路径
# 支持包含空格的路径
resolve_path() {
    local path="$1"
    local base_dir="${2:-$WORK_DIR}"
    
    # 去除路径两端的空格
    path=$(echo "$path" | xargs)
    
    # 如果路径是绝对路径，则直接使用
    if [[ "$path" == /* ]]; then
        echo "$path"
    else
        # 否则，将其解析为相对于工作目录的路径
        # 使用cd命令确保路径处理的准确性，支持空格
        local resolved_path="$(cd "$base_dir" && pwd)/$path"
        echo "$resolved_path"
    fi
}

# 处理关键路径参数，将可能为相对路径的变量转换为绝对路径:
log "INFO" "开始处理路径参数"

# 先解析WORK_DIR，因为其他路径依赖于它
WORK_DIR=$(resolve_path "$WORK_DIR" "${PWD}")
log "INFO" "解析后的工作目录: $WORK_DIR"

# 解析MDP_DIR
MDP_DIR=$(resolve_path "$MDP_DIR")
log "INFO" "解析后的MDP目录: $MDP_DIR"

# 检查MDP目录是否存在
check_dir_exists "MDP_DIR"

# 解析蛋白质列表
PROTEIN_LIST_RESOLVED=""
IFS=',' read -ra PATHS <<< "$PROTEIN_LIST"
valid_proteins=()

for i in "${!PATHS[@]}"; do
    raw_path="${PATHS[$i]}"
    clean_path=$(echo "$raw_path" | xargs)  # 去除空格
    
    if [ -n "$clean_path" ]; then  # 跳过空路径
        resolved_path=$(resolve_path "$clean_path")
        
        # 检查蛋白质文件是否存在
        if [ -f "$resolved_path" ]; then
            valid_proteins+=("$resolved_path")
            log "INFO" "解析并验证蛋白质文件: $resolved_path"
        else
            log "ERROR" "蛋白质文件不存在: $resolved_path"
            exit 1
        fi
    fi
done

# 如果没有有效的蛋白质文件，退出
if [ ${#valid_proteins[@]} -eq 0 ]; then
    log "ERROR" "没有找到有效的蛋白质文件"
    exit 1
fi

PROTEIN_LIST_RESOLVED=$(IFS=','; echo "${valid_proteins[*]}")
log "INFO" "所有蛋白质文件已解析完成，共 ${#valid_proteins[@]} 个文件"

# === 路径处理结束 ===

# 创建主工作目录
log "INFO" "创建主工作目录: $WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR" || exit 1
log "INFO" "已切换到工作目录: $WORK_DIR"

# 函数：检查文件是否存在，如果存在则跳过步骤
check_and_skip() {
    local file="$1"
    local step="$2"
    if [ "$SKIP_EXISTING" = "true" ] && [ -f "$file" ]; then
        log "INFO" "跳过 $step - 文件已存在: $file"
        return 0
    else
        return 1
    fi
}

# 函数：运行GROMACS命令，检查是否成功
run_gmx() {
    local cmd="$1"
    local output_file="$2"
    local step="$3"
    
    log "INFO" "开始执行: $step"
    log "DEBUG" "执行命令: $cmd"
    
    if eval "$cmd"; then
        if [ -n "$output_file" ]; then
            if [ -f "$output_file" ]; then
                log "INFO" "完成: $step"
                return 0
            else
                log "WARNING" "命令执行成功但输出文件未找到: $output_file"
                return 1
            fi
        else
            log "INFO" "完成: $step"  # 没有输出文件要求
            return 0
        fi
    else
        log "ERROR" "失败: $step"
        log "ERROR" "失败的命令: $cmd"
        return 1
    fi
}

# 处理每个蛋白质
log "INFO" "开始处理蛋白质列表"
IFS=',' read -ra PROTEINS <<< "$PROTEIN_LIST_RESOLVED"
total_proteins=${#PROTEINS[@]}
current_protein=0

for protein in "${PROTEINS[@]}"; do
    current_protein=$((current_protein + 1))
    protein=$(echo "$protein" | xargs)  # 去除空格
    
    if [ -z "$protein" ]; then
        log "WARNING" "跳过空蛋白质路径"
        continue
    fi
    
    protein_name=$(basename "$protein" .pdb)
    protein_dir="${WORK_DIR}/${protein_name}"
    
    log "INFO" "处理蛋白质 $current_protein/$total_proteins: $protein_name"
    log "INFO" "蛋白质文件路径: $protein"
    log "INFO" "蛋白质工作目录: $protein_dir"
    
    mkdir -p "$protein_dir"
    cd "$protein_dir" || exit 1
    
    # 步骤1: 准备蛋白质结构
    STEP_DIR="01_preparation"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    

    log "INFO" "开始蛋白质结构预处理：${protein_name}"

    # 1. 原始PDB文件
    #RAW_PDB="${PROTEIN_DIR}/${protein_name}.pdb"
    # 2. PropKa处理后的PDB（按指定pH质子化）
    TEMP_PQR="${protein_name}_temp.pqr"
    PROPKA_PDB="${protein_name}_propka.pdb"
    # 3. 去水后的干净PDB
    CLEAN_PDB="${protein_name}_clean.pdb"
    # 输出文件
    PROCESSED_GRO="${protein_name}_processed.gro"
    TOPOL_FILE="${protein_name}_topol.top"

    #if ! check_and_skip "$PROPKA_PDB" "PDB2PQR pH=${PH_VALUE} 质子化处理"; then
    if false; then
        log "INFO" "使用 PDB2PQR + PropKa 在 pH=${PH_VALUE} 下生成质子化结构"
        run_gmx "pdb2pqr --ff AMBER --titration-state-method propka --with-ph ${PH_VALUE} --pdb-output ${PROPKA_PDB} ${protein} ${TEMP_PQR}" \
                "$PROPKA_PDB" "质子化处理"
    else
        log "WARNING" "未实装其他质子化处理方法，不调整质子化状态，直接进行下一步"
        PROPKA_PDB=$protein
    fi

    # 1.2 自动去除水分子（HOH）
    if ! check_and_skip "$CLEAN_PDB" "去除水分子"; then
        run_gmx "grep -v HOH $PROPKA_PDB > $CLEAN_PDB" \
                "$CLEAN_PDB" "去除水分子"
    else
        log "INFO" "跳过去除水分子步骤，直接进行下一步"
        CLEAN_PDB=$PROPKA_PDB
    fi

    # 1.3 pdb2gmx 生成拓扑
    if ! check_and_skip "$PROCESSED_GRO" "pdb2gmx转换"; then
        run_gmx "gmx pdb2gmx -f \"$CLEAN_PDB\" -o \"$PROCESSED_GRO\" -p \"$TOPOL_FILE\" -water \"$WATER_MODEL\" -ff \"$FORCE_FIELD\" -ignh" \
                "$PROCESSED_GRO" "pdb2gmx转换"
    fi



    
    # 步骤2: 定义模拟盒子
    STEP_DIR="../02_box"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    BOX_GRO="${protein_name}_waterbox.gro"
    if ! check_and_skip "$BOX_GRO" "盒子定义"; then
        run_gmx "gmx editconf -f '../01_preparation/$PROCESSED_GRO' -o '$BOX_GRO' -c -d '$BOX_DISTANCE' -bt '$BOX_TYPE'" \
                "$BOX_GRO" "编辑盒子"
    fi
    
    # 步骤3: 溶剂化
    STEP_DIR="../03_solvation"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    SOLV_GRO="${protein_name}_solv.gro"
    if ! check_and_skip "$SOLV_GRO" "溶剂化"; then
        run_gmx "gmx solvate -cp '../02_box/$BOX_GRO' -cs spc216.gro -o '$SOLV_GRO' -p '../01_preparation/$TOPOL_FILE'" \
                "$SOLV_GRO" "溶剂化"
    fi
    
    # 步骤4: 添加离子
    STEP_DIR="../04_ions"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    IONS_TPR="${protein_name}_ions.tpr"
    SOLV_IONS_GRO="${protein_name}_solv_ions.gro"
    
    # 4.1 准备离子添加
    if ! check_and_skip "$IONS_TPR" "离子预处理"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/ions.mdp' -c '../03_solvation/$SOLV_GRO' -p '../01_preparation/$TOPOL_FILE' -o '$IONS_TPR'" \
                "$IONS_TPR" "grompp离子准备"
    fi
    
    # 4.2 添加离子
    if ! check_and_skip "$SOLV_IONS_GRO" "添加离子"; then
        run_gmx "echo 'SOL' | gmx genion -s '$IONS_TPR' -o '$SOLV_IONS_GRO' -p '../01_preparation/$TOPOL_FILE' -pname '$POSITIVE_ION' -nname '$NEGATIVE_ION' -conc '$ION_CONCENTRATION' -neutral" \
                "$SOLV_IONS_GRO" "添加离子"
    fi
    
    # 步骤5: 能量最小化
    STEP_DIR="../05_energy_minimization"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    EM_TPR="${protein_name}_em.tpr"
    EM_GRO="${protein_name}_em.gro"
    # EM_EDR="${protein_name}_em.edr"
    # EM_LOG="${protein_name}_em.log"
    
    # 5.1 准备能量最小化
    if ! check_and_skip "$EM_TPR" "能量最小化准备"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/minim.mdp' -c '../04_ions/$SOLV_IONS_GRO' -p '../01_preparation/$TOPOL_FILE' -o '$EM_TPR'" \
                "$EM_TPR" "grompp能量最小化"
    fi
    
    # 5.2 运行能量最小化
    if ! check_and_skip "$EM_GRO" "能量最小化运行"; then
        EM_CMD="gmx mdrun -deffnm '${protein_name}_em' -s '$EM_TPR'"

        [ "$EM_VERBOSE" = "true" ] && EM_CMD="$EM_CMD -v"
        [ -n "${NUM_THREADS:-}" ] && EM_CMD="$EM_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && EM_CMD="$EM_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && EM_CMD="$EM_CMD $EXTRA_ARGS"

        run_gmx "$EM_CMD" "$EM_GRO" "能量最小化"
    fi
    
    # 步骤6: NVT平衡
    STEP_DIR="../06_nvt_equilibration"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    NVT_TPR="${protein_name}_nvt.tpr"
    NVT_GRO="${protein_name}_nvt.gro"
    NVT_CPT="${protein_name}_nvt.cpt"
    
    if ! check_and_skip "$NVT_GRO" "NVT平衡"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/nvt.mdp' -c '../05_energy_minimization/$EM_GRO' -r '../05_energy_minimization/$EM_GRO' -p '../01_preparation/$TOPOL_FILE' -o '$NVT_TPR'" \
                "$NVT_TPR" "grompp NVT准备"
        
        NVT_CMD="gmx mdrun -deffnm '${protein_name}_nvt' -s '$NVT_TPR'"

        [ "$EQUIL_VERBOSE" = "true" ] && NVT_CMD="$NVT_CMD -v"
        [ -n "${NUM_THREADS:-}" ] && NVT_CMD="$NVT_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && NVT_CMD="$NVT_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && NVT_CMD="$NVT_CMD $EXTRA_ARGS"

        run_gmx "$NVT_CMD" "$NVT_GRO" "NVT平衡"
    fi
    
    # 步骤7: NPT平衡
    STEP_DIR="../07_npt_equilibration"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    NPT_TPR="${protein_name}_npt.tpr"
    NPT_GRO="${protein_name}_npt.gro"
    NPT_CPT="${protein_name}_npt.cpt"
    
    if ! check_and_skip "$NPT_GRO" "NPT平衡"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/npt.mdp' -c '../06_nvt_equilibration/$NVT_GRO' -r '../06_nvt_equilibration/$NVT_GRO' -t '../06_nvt_equilibration/$NVT_CPT' -p '../01_preparation/$TOPOL_FILE' -o '$NPT_TPR'" \
                "$NPT_TPR" "grompp NPT准备"
        
        NPT_CMD="gmx mdrun -deffnm '${protein_name}_npt' -s '$NPT_TPR'"
        
        [ "$EQUIL_VERBOSE" = "true" ] && NPT_CMD="$NPT_CMD -v"
        [ -n "${NUM_THREADS:-}" ] && NPT_CMD="$NPT_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && NPT_CMD="$NPT_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && NPT_CMD="$NPT_CMD $EXTRA_ARGS"

        run_gmx "$NPT_CMD" "$NPT_GRO" "NPT平衡"
    fi
    
    # 步骤8: 分子动力学模拟
    STEP_DIR="../08_md_production"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    MD_TPR="${protein_name}_md.tpr"
    MD_GRO="${protein_name}_md.gro"
    MD_XTC="${protein_name}_md.xtc"
    
    if ! check_and_skip "$MD_GRO" "MD模拟"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/md.mdp' -c '../07_npt_equilibration/$NPT_GRO' -t '../07_npt_equilibration/$NPT_CPT' -p '../01_preparation/$TOPOL_FILE' -o '$MD_TPR'" \
                "$MD_TPR" "grompp MD准备"
        
        MD_CMD="gmx mdrun -deffnm '${protein_name}_md' -s '$MD_TPR'"
        
        [ -n "${NUM_THREADS:-}" ] && MD_CMD="$MD_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && MD_CMD="$MD_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && MD_CMD="$MD_CMD $EXTRA_ARGS"
        
        run_gmx "$MD_CMD" \
                "$MD_GRO" "MD模拟"
    fi

    # 步骤9: 轨迹周期性校正
    # 遵循官方推荐工作流：
        # 1. 使分子完整 (Whole)
        # 2. 聚类 (Cluster, 可选)
        # 3. 去跳跃 (NoJump, 需第一帧参考)
        # 4. 中心化 (Center)
        # 5. 盒子调整 (PBC + Unit Cell)
    # 调用其他脚本完成此步骤。脚本路径为../MD-complex/traj_correction_module.sh
    if [ "$PERFORM_TRAJ_CORRECTION" = "true" ] && declare -f perform_traj_correction &> /dev/null; then
        STEP_DIR="../09_traj_correction"
        mkdir -p "$STEP_DIR"
        cd "$STEP_DIR"

        perform_traj_correction \
            "../08_md_production/${MD_TPR}" \
            "../08_md_production/${MD_XTC}" \
            "../08_md_production/${MD_GRO}" \
            "${protein_name}_md" \
            "$CENTER_GROUP" \
            "$OUTPUT_GROUP" \
            "$TRAJ_CORRECTION_METHOD" \
            "$SKIP_EXISTING"

    fi
done

# 生成模拟结果汇总
generate_summary() {
    log "INFO" "生成模拟结果汇总..."
    SUMMARY_FILE="${WORK_DIR}/md_summary.txt"
    
    echo "# GROMACS MD模拟结果汇总" > "$SUMMARY_FILE"
    echo "生成时间: $(date)" >> "$SUMMARY_FILE"
    echo "总蛋白质数量: $total_proteins" >> "$SUMMARY_FILE"
    echo "成功处理: $total_proteins" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo "# 详细结果" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
    
    for protein in "${PROTEINS[@]}"; do
        local protein_name=$(basename "$protein" .pdb)
        echo "- 蛋白质: $protein_name" >> "$SUMMARY_FILE"
        echo "  状态: 完成" >> "$SUMMARY_FILE"
        echo "  工作目录: ${WORK_DIR}/${protein_name}" >> "$SUMMARY_FILE"
        echo "" >> "$SUMMARY_FILE"
    done
    
    log "INFO" "结果汇总已保存至: $SUMMARY_FILE"
}

generate_summary

log "INFO" "所有蛋白质处理完成"
log "INFO" "模拟日志已保存至: $LOG_FILE"
log "INFO" "模拟结果汇总已保存至: ${WORK_DIR}/md_summary.txt"