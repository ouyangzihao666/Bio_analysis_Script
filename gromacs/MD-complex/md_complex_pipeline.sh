#!/bin/bash
# GROMACS 蛋白-配体复合物MD模拟自动化脚本（最终稳定版）
set -euo pipefail

# ====================== 基础功能函数 ======================

# 获取当前用户路径
USER_CWD="$PWD"

log() {
    local level="$1"
    local message="$2"
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $level: $message" | tee -a "$LOG_FILE"
}

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

# ====================== 配体处理函数 ======================
process_ligand() {
    local ligand_sdf="$1"
    local ligand_name="$2"
    local out_dir="$3"
    
    mkdir -p "$out_dir"
    cd "$out_dir" || exit 1
    
    local LIGAND_SDF_H="${ligand_name}_H.sdf"
    local LIGAND_MOL2="${ligand_name}.mol2"
    local LIGAND_ITP="${ligand_name}_GMX.itp"
    local LIGAND_GRO="${ligand_name}_GMX.gro"
    
    if ! check_and_skip "$LIGAND_ITP" "配体${ligand_name}拓扑生成"; then
        log "INFO" "用OpenBabel预处理配体：加氢、优化3D结构"
        run_gmx "obabel '$ligand_sdf' -O '$LIGAND_SDF_H' -h --gen3d" \
                "$LIGAND_SDF_H" "OpenBabel配体预处理"
        
        log "INFO" "开始执行: 配体电荷计算"
        run_gmx "antechamber -i '$LIGAND_SDF_H' -o '$LIGAND_MOL2' -fi sdf -fo mol2 -at '$LIGAND_FORCE_FIELD' -c '$CHARGE_METHOD' -s 2 -nc '$LIGAND_NET_CHARGE'" \
                "$LIGAND_MOL2" "配体电荷计算"
        
        log "INFO" "开始执行: 配体拓扑转换为GROMACS格式"
        log "DEBUG" "执行命令: acpype -i '$LIGAND_MOL2'"
        
        if acpype -i "$LIGAND_MOL2"; then
            local acpype_dir="${ligand_name}.acpype"
            if [ -d "$acpype_dir" ]; then
                cp -f "$acpype_dir/${ligand_name}_GMX.itp" .
                cp -f "$acpype_dir/${ligand_name}_GMX.gro" .
                log "INFO" "完成: 配体拓扑转换为GROMACS格式"
            fi
        fi
    fi
    
}

# ====================== 结构合并函数 ======================
merge_structure() {
    local protein_gro="$1"
    local ligand_gro="$2"
    local output_gro="$3"
    
    if ! check_and_skip "$output_gro" "蛋白-配体结构合并"; then
        local prot_atoms=$(sed -n '2p' "$protein_gro" | xargs)
        local lig_atoms=$(sed -n '2p' "$ligand_gro" | xargs)
        local total_atoms=$((prot_atoms + lig_atoms))
        
        {
            head -n 1 "$protein_gro"
            echo "$total_atoms"
            sed -n "3,$((2+prot_atoms))p" "$protein_gro"
            sed -n "3,$((2+lig_atoms))p" "$ligand_gro"
            tail -n 1 "$protein_gro"
        } > "$output_gro"
        log "INFO" "结构合并完成，总原子数: $total_atoms"
    fi
}

# ====================== 拓扑合并函数 ======================
merge_topology() {
    local protein_top="$1"
    local ligand_itp="$2"
    local ligand_name="$3"
    local output_top="$4"
    
    if ! check_and_skip "$output_top" "蛋白-配体拓扑合并"; then
        sed "/#include.*forcefield.itp/a #include \"$ligand_itp\"" "$protein_top" > tmp_top.top
        sed -i "\$a $ligand_name              1" tmp_top.top
        mv tmp_top.top "$output_top"
        log "INFO" "拓扑合并完成，已添加配体: $ligand_name"
    fi
}

# ====================== 加载轨迹校正函数 ======================
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

# ====================== 环境与参数初始化 ======================
# 1. 检查GROMACS环境
if ! command -v gmx &> /dev/null; then
    echo "错误：未找到gmx命令，请先加载GROMACS环境！"
    exit 1
fi

# 2. 检查配体工具环境
required_tools=("antechamber" "parmchk2" "acpype" "obabel")
for tool in "${required_tools[@]}"; do
    if ! command -v "$tool" &> /dev/null; then
        echo "错误：未找到$tool命令 ，请执行 conda install -c conda-forge ambertools acpype openbabel -y 安装"
        exit 1
    fi
done

# 3. 解析命令行参数
CONFIG_FILE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) echo "Usage: $0 <config_file>"; exit 0 ;;
        *) CONFIG_FILE="$1"; shift ;;
    esac
done

if [ -z "$CONFIG_FILE" ] || [ ! -f "$CONFIG_FILE" ]; then
    echo "错误：必须指定有效的配置文件"
    exit 1
fi

# 4. 加载默认参数
SKIP_EXISTING="true"
EM_VERBOSE="false"
EQUIL_VERBOSE="false"
PH_VALUE="7.4"
MAXWARN="5"
LIGAND_FORCE_FIELD="gaff2"
CHARGE_METHOD="bcc"
LIGAND_NET_CHARGE="0"
PERFORM_TRAJ_CORRECTION="true"
CENTER_GROUP="Protein"
OUTPUT_GROUP="System"

# 5. 加载配置文件
source "$CONFIG_FILE"

# 6. 初始化日志
WORK_DIR=$(realpath "$WORK_DIR")
mkdir -p "$WORK_DIR"
LOG_FILE="${WORK_DIR}/md_complex_pipeline_$(date +%Y%m%d_%H%M%S).log"
touch "$LOG_FILE"
log "INFO" "===== 蛋白-配体复合物MD模拟脚本开始执行 ====="
log "INFO" "使用配置文件: $CONFIG_FILE"
log "INFO" "工作目录: $WORK_DIR"

# 7. 验证文件
if [ ! -f "$GROUP_FILE" ]; then log "ERROR" "group文件不存在: $GROUP_FILE"; exit 1; fi
MDP_DIR=$(realpath "$MDP_DIR")
if [ ! -d "$MDP_DIR" ]; then log "ERROR" "MDP目录不存在: $MDP_DIR"; exit 1; fi

# 8. 加载轨迹校正模块
source_traj_module

# ====================== 主流程 ======================
mapfile -t COMPLEX_LINES < <(grep -v '^#' "$GROUP_FILE" | grep -v '^$')
TOTAL_COMPLEX=${#COMPLEX_LINES[@]}

if [ $TOTAL_COMPLEX -eq 0 ]; then log "ERROR" "group文件中未找到有效复合物配对"; exit 1; fi
log "INFO" "共检测到 $TOTAL_COMPLEX 个复合物体系"

for line in "${COMPLEX_LINES[@]}"; do
    IFS=',' read -ra FIELDS <<< "$line"
    PROTEIN_PDB=$(realpath "${FIELDS[0]}" | xargs)
    LIGAND_SDF=$(realpath "${FIELDS[1]}" | xargs)
    PROTEIN_NAME=$(echo "${FIELDS[2]}" | xargs)
    LIGAND_NAME=$(echo "${FIELDS[3]}" | xargs)

    SYSTEM_NAME="${SYSTEM_NAME}_${LIGAND_NAME}"

    log "INFO" "===== 处理体系: ${SYSTEM_NAME} ====="
    log "INFO" "蛋白文件: $PROTEIN_PDB"
    log "INFO" "配体文件: $LIGAND_SDF"

    if [ ! -f "$PROTEIN_PDB" ] || [ ! -f "$LIGAND_SDF" ]; then
        log "ERROR" "输入文件不存在"
        exit 1
    fi

    SYSTEM_DIR="${WORK_DIR}/${SYSTEM_NAME}"
    mkdir -p "$SYSTEM_DIR"
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤1：蛋白预处理 ======================
    STEP_DIR="01_protein_prep"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1

    PROTEIN_CLEAN="${PROTEIN_NAME}_clean.pdb"
    PROTEIN_GRO="${PROTEIN_NAME}_processed.gro"
    PROTEIN_TOP="${PROTEIN_NAME}_topol.top"

    if ! check_and_skip "$PROTEIN_GRO" "蛋白预处理"; then
        run_gmx "grep -v HOH '$PROTEIN_PDB' > '$PROTEIN_CLEAN'" \
                "$PROTEIN_CLEAN" "蛋白去水"
        run_gmx "gmx pdb2gmx -f '$PROTEIN_CLEAN' -o '$PROTEIN_GRO' -p '$PROTEIN_TOP' -water '$WATER_MODEL' -ff '$FORCE_FIELD' -ignh" \
                "$PROTEIN_GRO" "蛋白拓扑生成"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤2：配体预处理 ======================
    STEP_DIR="02_ligand_prep"
    process_ligand "$LIGAND_SDF" "$LIGAND_NAME" "$STEP_DIR"
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤3：蛋白-配体合并 ======================
    
    STEP_DIR="03_complex_merge"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1

    COMPLEX_GRO="${SYSTEM_NAME}_complex.gro"
    COMPLEX_TOP="${SYSTEM_NAME}_complex.top"

    # 明确定义所有文件的绝对路径
    ABS_PROTEIN_GRO="${SYSTEM_DIR}/01_protein_prep/${PROTEIN_GRO}"
    ABS_LIGAND_GRO="${SYSTEM_DIR}/02_ligand_prep/${LIGAND_NAME}_GMX.gro"
    ABS_PROTEIN_TOP="${SYSTEM_DIR}/01_protein_prep/${PROTEIN_TOP}"
    ABS_LIGAND_ITP="${SYSTEM_DIR}/02_ligand_prep/${LIGAND_NAME}_GMX.itp"

        # 合并拓扑
    if ! check_and_skip "$COMPLEX_TOP" "蛋白-配体拓扑合并"; then
        log "INFO" "开始执行: 蛋白-配体拓扑合并"
        
        # 检查输入文件是否存在
        if [ ! -f "$ABS_PROTEIN_TOP" ]; then
            log "ERROR" "蛋白拓扑文件不存在: $ABS_PROTEIN_TOP"
            exit 1
        fi
        if [ ! -f "$ABS_LIGAND_ITP" ]; then
            log "ERROR" "配体拓扑文件不存在: $ABS_LIGAND_ITP"
            exit 1
        fi
        
        # 复制posre.itp到当前目录（修复位置约束文件路径问题）
        ABS_POSRE_ITP="${SYSTEM_DIR}/01_protein_prep/posre.itp"
        if [ -f "$ABS_POSRE_ITP" ]; then
            cp -f "$ABS_POSRE_ITP" .
            log "INFO" "已复制位置约束文件: posre.itp"
        fi
        
        # 合并结构
        log "INFO" "合并结构..."
        prot_atoms=$(sed -n '2p' "$ABS_PROTEIN_GRO" | xargs)
        lig_atoms=$(sed -n '2p' "$ABS_LIGAND_GRO" | xargs)
        total_atoms=$((prot_atoms + lig_atoms))

        {
            head -n 1 "$ABS_PROTEIN_GRO"
            echo "$total_atoms"
            sed -n "3,$((2+prot_atoms))p" "$ABS_PROTEIN_GRO"
            sed -n "3,$((2+lig_atoms))p" "$ABS_LIGAND_GRO"
            tail -n 1 "$ABS_PROTEIN_GRO"
        } > "$COMPLEX_GRO"
        log "INFO" "结构合并完成，总原子数: $total_atoms"
        
        # 合并拓扑
        sed "/#include.*forcefield.itp/a #include \"$ABS_LIGAND_ITP\"" "$ABS_PROTEIN_TOP" > tmp_top.top
        sed -i "\$a $LIGAND_NAME              1" tmp_top.top
        mv tmp_top.top "$COMPLEX_TOP"

        log "INFO" "完成: 蛋白-配体拓扑合并，已添加配体: $LIGAND_NAME"
    fi

    

    # 回到体系目录
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤4：盒子 ======================
    STEP_DIR="04_box"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    BOX_GRO="${SYSTEM_NAME}_waterbox.gro"
    if ! check_and_skip "$BOX_GRO" "盒子定义"; then
        run_gmx "gmx editconf -f '../03_complex_merge/$COMPLEX_GRO' -o '$BOX_GRO' -c -d '$BOX_DISTANCE' -bt '$BOX_TYPE'" \
                "$BOX_GRO" "编辑盒子"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤5：溶剂化 ======================
    STEP_DIR="05_solvation"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    SOLV_GRO="${SYSTEM_NAME}_solv.gro"
    if ! check_and_skip "$SOLV_GRO" "溶剂化"; then
        run_gmx "gmx solvate -cp '../04_box/$BOX_GRO' -cs spc216.gro -o '$SOLV_GRO' -p '../03_complex_merge/$COMPLEX_TOP'" \
                "$SOLV_GRO" "溶剂化"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤6：离子 ======================
    STEP_DIR="06_ions"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    IONS_TPR="${SYSTEM_NAME}_ions.tpr"
    SOLV_IONS_GRO="${SYSTEM_NAME}_solv_ions.gro"
    if ! check_and_skip "$IONS_TPR" "离子预处理"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/ions.mdp' -c '../05_solvation/$SOLV_GRO' -p '../03_complex_merge/$COMPLEX_TOP' -o '$IONS_TPR'" \
                "$IONS_TPR" "grompp离子准备"
    fi
    if ! check_and_skip "$SOLV_IONS_GRO" "添加离子"; then
        run_gmx "echo 'SOL' | gmx genion -s '$IONS_TPR' -o '$SOLV_IONS_GRO' -p '../03_complex_merge/$COMPLEX_TOP' -pname '$POSITIVE_ION' -nname '$NEGATIVE_ION' -conc '$ION_CONCENTRATION' -neutral" \
                "$SOLV_IONS_GRO" "添加离子"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤7：能量最小化 ======================
    STEP_DIR="07_energy_minimization"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    EM_TPR="${SYSTEM_NAME}_em.tpr"
    EM_GRO="${SYSTEM_NAME}_em.gro"
    if ! check_and_skip "$EM_TPR" "能量最小化准备"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/minim.mdp' -c '../06_ions/$SOLV_IONS_GRO' -p '../03_complex_merge/$COMPLEX_TOP' -o '$EM_TPR'" \
                "$EM_TPR" "grompp能量最小化"
    fi
    if ! check_and_skip "$EM_GRO" "能量最小化运行"; then
        EM_CMD="gmx mdrun -deffnm '${SYSTEM_NAME}_em' -s '$EM_TPR'"
        [ "$EM_VERBOSE" = "true" ] && EM_CMD="$EM_CMD -v"
        [ -n "${NUM_THREADS:-}" ] && EM_CMD="$EM_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && EM_CMD="$EM_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && EM_CMD="$EM_CMD $EXTRA_ARGS"
        run_gmx "$EM_CMD" "$EM_GRO" "能量最小化"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤8：NVT ======================
    STEP_DIR="08_nvt_equilibration"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    NVT_TPR="${SYSTEM_NAME}_nvt.tpr"
    NVT_GRO="${SYSTEM_NAME}_nvt.gro"
    if ! check_and_skip "$NVT_GRO" "NVT平衡"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/nvt.mdp' -c '../07_energy_minimization/$EM_GRO' -r '../07_energy_minimization/$EM_GRO' -p '../03_complex_merge/$COMPLEX_TOP' -o '$NVT_TPR'" \
                "$NVT_TPR" "grompp NVT准备"
        NVT_CMD="gmx mdrun -deffnm '${SYSTEM_NAME}_nvt' -s '$NVT_TPR'"
        [ "$EQUIL_VERBOSE" = "true" ] && NVT_CMD="$NVT_CMD -v"
        [ -n "${NUM_THREADS:-}" ] && NVT_CMD="$NVT_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && NVT_CMD="$NVT_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && NVT_CMD="$NVT_CMD $EXTRA_ARGS"
        run_gmx "$NVT_CMD" "$NVT_GRO" "NVT平衡"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤9：NPT ======================
    STEP_DIR="09_npt_equilibration"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    NPT_TPR="${SYSTEM_NAME}_npt.tpr"
    NPT_GRO="${SYSTEM_NAME}_npt.gro"
    NPT_CPT="${SYSTEM_NAME}_npt.cpt"
    if ! check_and_skip "$NPT_GRO" "NPT平衡"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/npt.mdp' -c '../08_nvt_equilibration/$NVT_GRO' -r '../08_nvt_equilibration/$NVT_GRO' -t '../08_nvt_equilibration/${SYSTEM_NAME}_nvt.cpt' -p '../03_complex_merge/$COMPLEX_TOP' -o '$NPT_TPR'" \
                "$NPT_TPR" "grompp NPT准备"
        NPT_CMD="gmx mdrun -deffnm '${SYSTEM_NAME}_npt' -s '$NPT_TPR'"
        [ "$EQUIL_VERBOSE" = "true" ] && NPT_CMD="$NPT_CMD -v"
        [ -n "${NUM_THREADS:-}" ] && NPT_CMD="$NPT_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && NPT_CMD="$NPT_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && NPT_CMD="$NPT_CMD $EXTRA_ARGS"
        run_gmx "$NPT_CMD" "$NPT_GRO" "NPT平衡"
    fi
    cd "$SYSTEM_DIR" || exit 1

    # ====================== 步骤10：生产MD ======================
    STEP_DIR="10_md_production"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR" || exit 1
    MD_TPR="${SYSTEM_NAME}_md.tpr"
    MD_GRO="${SYSTEM_NAME}_md.gro"
    if ! check_and_skip "$MD_GRO" "MD生产模拟"; then
        run_gmx "gmx grompp -maxwarn '$MAXWARN' -f '${MDP_DIR}/md.mdp' -c '../09_npt_equilibration/$NPT_GRO' -t '../09_npt_equilibration/$NPT_CPT' -p '../03_complex_merge/$COMPLEX_TOP' -o '$MD_TPR'" \
                "$MD_TPR" "grompp MD准备"
        MD_CMD="gmx mdrun -deffnm '${SYSTEM_NAME}_md' -s '$MD_TPR'"
        [ -n "${NUM_THREADS:-}" ] && MD_CMD="$MD_CMD -nt $NUM_THREADS"
        [ -n "${GPU_ID:-}" ] && MD_CMD="$MD_CMD -gpu_id $GPU_ID"
        [ -n "${EXTRA_ARGS:-}" ] && MD_CMD="$MD_CMD $EXTRA_ARGS"
        run_gmx "$MD_CMD" "$MD_GRO" "MD生产模拟"
    fi

        # ====================== 步骤11：轨迹周期性校正 ======================
    if [ "$PERFORM_TRAJ_CORRECTION" = "true" ] && declare -f perform_traj_correction &> /dev/null; then
        STEP_DIR="11_traj_correction"
        mkdir -p "$STEP_DIR"
        cd "$STEP_DIR" || exit 1

        perform_traj_correction \
            "../10_md_production/${SYSTEM_NAME}_md.tpr" \
            "../10_md_production/${SYSTEM_NAME}_md.xtc" \
            "../10_md_production/${SYSTEM_NAME}_md.gro" \
            "${SYSTEM_NAME}_md" \
            "$CENTER_GROUP" \
            "$OUTPUT_GROUP" \
            "$TRAJ_CORRECTION_METHOD" \
            "$SKIP_EXISTING"

        cd "$SYSTEM_DIR" || exit 1
    fi
    # ====================================================================

    log "INFO" "===== 体系 ${SYSTEM_NAME} 处理完成 ====="
    cd "$USER_CWD" || exit 1
done

log "INFO" "所有复合物体系处理完成！"
log "INFO" "模拟日志: $LOG_FILE"