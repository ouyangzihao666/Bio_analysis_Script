#!/bin/bash

# GROMACS分子动力学模拟批处理脚本
# 使用方法: ./run_md_pipeline.sh <config_file>

# 错误处理设置
set -euo pipefail

# 检查参数
if [ $# -ne 1 ]; then
    echo "用法: $0 <配置文件>"
    exit 1
fi

CONFIG_FILE="$1"

# ========== 首先设置所有默认值 ==========
SKIP_EXISTING="true"
EM_VERBOSE="true"
EQUIL_VERBOSE="true"
SOLVENT_FILE="spc216.gro"

# 轨迹校正相关参数的默认值
PERFORM_TRAJ_CORRECTION="true"
TRAJ_CORRECTION_METHOD="pbc_mol_center"
CENTER_GROUP="Protein"
OUTPUT_GROUP="System"
CORRECTED_TRAJ_SUFFIX="_md_corrected.xtc"

# 加载配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "错误: 配置文件 $CONFIG_FILE 不存在"
    exit 1
fi
source "$CONFIG_FILE"

# 检查必要参数
check_required_param() {
    if [ -z "${!1}" ]; then
        echo "错误: 配置文件中缺少必要参数: $1"
        exit 1
    fi
}

required_params=("PROTEIN_LIST" "WORK_DIR" "MDP_DIR" "WATER_MODEL" "FORCE_FIELD" "BOX_TYPE" "BOX_DISTANCE" "ION_CONCENTRATION")
for param in "${required_params[@]}"; do
    check_required_param "$param"
done

# === 路径处理函数 ===
# 将相对路径转换为基于工作目录的绝对路径
resolve_path() {
    local path="$1"
    # 如果路径是绝对路径，则直接使用
    if [[ "$path" == /* ]]; then
        echo "$path"
    else
        # 否则，将其解析为相对于工作目录的路径
        echo "${WORK_DIR}/${path}"
    fi
}

# 处理关键路径参数，将可能为相对路径的变量转换为绝对路径:
PROTEIN_LIST_RESOLVED=""
IFS=',' read -ra PATHS <<< "$PROTEIN_LIST"
for i in "${!PATHS[@]}"; do
    PATHS[$i]=$(resolve_path "${PATHS[$i]}")
done
PROTEIN_LIST_RESOLVED=$(IFS=','; echo "${PATHS[*]}")

WORK_DIR=$(resolve_path "$WORK_DIR")
MDP_DIR=$(resolve_path "$MDP_DIR")

# === 路径处理结束 ===

# 创建主工作目录
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# 函数：检查文件是否存在，如果存在则跳过步骤
check_and_skip() {
    local file="$1"
    local step="$2"
    if [ "$SKIP_EXISTING" = "true" ] && [ -f "$file" ]; then
        echo "跳过 $step - 文件已存在: $file"
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
    
    echo "运行: $step"
    if eval "$cmd"; then
        if [ -n "$output_file" ] && [ -f "$output_file" ]; then
            echo "完成: $step"
            return 0
        else
            echo "警告: 命令执行成功但输出文件未找到: $output_file"
            return 1
        fi
    else
        echo "错误: $step 失败"
        return 1
    fi
}

# 处理每个蛋白质
IFS=',' read -ra PROTEINS <<< "$PROTEIN_LIST_RESOLVED"
for protein in "${PROTEINS[@]}"; do
    protein=$(echo "$protein" | xargs)  # 去除空格
    if [ -z "$protein" ]; then
        continue
    fi
    
    protein_name=$(basename "$protein" .pdb)
    protein_dir="${WORK_DIR}/${protein_name}"
    
    echo "开始处理蛋白质: $protein_name"
    mkdir -p "$protein_dir"
    cd "$protein_dir"
    
    # 步骤1: 准备蛋白质结构
    STEP_DIR="01_preparation"
    mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"
    
    # 1.1 清理PDB文件（去除水）
    CLEAN_PDB="${protein_name}_clean.pdb"
    if ! check_and_skip "$CLEAN_PDB" "PDB清理"; then
        grep -v HOH "$protein" > "$CLEAN_PDB"
    fi
    
    # 1.2 PDB转GRO格式
    PROCESSED_GRO="${protein_name}_processed.gro"
    TOPOL_FILE="${protein_name}_topol.top"
    if ! check_and_skip "$PROCESSED_GRO" "pdb2gmx"; then
        run_gmx "gmx pdb2gmx -f '$CLEAN_PDB' -o '$PROCESSED_GRO' -p '$TOPOL_FILE' -water '$WATER_MODEL' -ff '$FORCE_FIELD' -ignh" \
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
        run_gmx "gmx grompp -f '${MDP_DIR}/ions.mdp' -c '../03_solvation/$SOLV_GRO' -p '../01_preparation/$TOPOL_FILE' -o '$IONS_TPR'" \
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
        run_gmx "gmx grompp -f '${MDP_DIR}/minim.mdp' -c '../04_ions/$SOLV_IONS_GRO' -p '../01_preparation/$TOPOL_FILE' -o '$EM_TPR'" \
                "$EM_TPR" "grompp能量最小化"
    fi
    
    # 5.2 运行能量最小化
    if ! check_and_skip "$EM_GRO" "能量最小化运行"; then
        EM_CMD="gmx mdrun -deffnm '${protein_name}_em' -s '$EM_TPR'"
        if [ "$EM_VERBOSE" = "true" ]; then
            EM_CMD="$EM_CMD -v"
        fi
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
        run_gmx "gmx grompp -f '${MDP_DIR}/nvt.mdp' -c '../05_energy_minimization/$EM_GRO' -r '../05_energy_minimization/$EM_GRO' -p '../01_preparation/$TOPOL_FILE' -o '$NVT_TPR'" \
                "$NVT_TPR" "grompp NVT准备"
        
        NVT_CMD="gmx mdrun -deffnm '${protein_name}_nvt' -s '$NVT_TPR'"
        if [ "$EQUIL_VERBOSE" = "true" ]; then
            NVT_CMD="$NVT_CMD -v"
        fi
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
        run_gmx "gmx grompp -f '${MDP_DIR}/npt.mdp' -c '../06_nvt_equilibration/$NVT_GRO' -r '../06_nvt_equilibration/$NVT_GRO' -t '../06_nvt_equilibration/$NVT_CPT' -p '../01_preparation/$TOPOL_FILE' -o '$NPT_TPR'" \
                "$NPT_TPR" "grompp NPT准备"
        
        NPT_CMD="gmx mdrun -deffnm '${protein_name}_npt' -s '$NPT_TPR'"
        if [ "$EQUIL_VERBOSE" = "true" ]; then
            NPT_CMD="$NPT_CMD -v"
        fi
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
        run_gmx "gmx grompp -f '${MDP_DIR}/md.mdp' -c '../07_npt_equilibration/$NPT_GRO' -t '../07_npt_equilibration/$NPT_CPT' -p '../01_preparation/$TOPOL_FILE' -o '$MD_TPR'" \
                "$MD_TPR" "grompp MD准备"
        
        MD_RUN_CMD="gmx mdrun -deffnm '${protein_name}_md' -s '$MD_TPR'"
        if [ -n "${NUM_THREADS:-}" ]; then
            MD_RUN_CMD="$MD_RUN_CMD -nt $NUM_THREADS"
        fi
        if [ -n "${GPU_ID:-}" ]; then
            MD_RUN_CMD="$MD_RUN_CMD -gpu_id $GPU_ID"
        fi
        
        run_gmx "$MD_RUN_CMD" \
                "$MD_GRO" "MD模拟"
    fi

    # 步骤9: 轨迹周期性校正
    STEP_DIR="../08_md_production"
    # mkdir -p "$STEP_DIR"
    cd "$STEP_DIR"

    CORRECTED_TRAJ="${protein_name}_md_noPBC.xtc"

    if ! check_and_skip "$CORRECTED_TRAJ" "轨迹周期性校正"; then
        if [ "$PERFORM_TRAJ_CORRECTION" = "true" ]; then
            echo "运行轨迹周期性校正..."
            
            case "$TRAJ_CORRECTION_METHOD" in
                "pbc_nojump")
                    # 无跳跃周期性校正
                    run_gmx "echo -e '${CENTER_GROUP}\\n${OUTPUT_GROUP}' | gmx trjconv -s '../08_md_production/${protein_name}_md.tpr' -f '../08_md_production/${protein_name}_md.xtc' -o '$CORRECTED_TRAJ' -pbc nojump -center" \
                            "$CORRECTED_TRAJ" "轨迹周期性校正(pbc nojump + center)"
                    ;;
                "pbc_atom")
                    # 原子周期性校正
                    run_gmx "echo -e '${CENTER_GROUP}\\n${OUTPUT_GROUP}' | gmx trjconv -s '../08_md_production/${protein_name}_md.tpr' -f '../08_md_production/${protein_name}_md.xtc' -o '$CORRECTED_TRAJ' -pbc atom -center" \
                            "$CORRECTED_TRAJ" "轨迹周期性校正(pbc atom + center)"
                    ;;
                "pbc_mol")
                    # 默认方法：分子周期性校正 + 居中
                    ;&
                *)
                    echo "默认轨迹校正方法"
                    run_gmx "echo -e '${CENTER_GROUP}\\n${OUTPUT_GROUP}' | gmx trjconv -s '../08_md_production/${protein_name}_md.tpr' -f '../08_md_production/${protein_name}_md.xtc' -o '$CORRECTED_TRAJ' -pbc mol -center" \
                            "$CORRECTED_TRAJ" "轨迹周期性校正(pbc mol + center)"
                    ;;
            esac
        else
            # 如果不执行校正，创建原始轨迹的符号链接
            echo "跳过轨迹校正，使用原始轨迹"
            ln -sf "../08_md_production/${protein_name}_md.xtc" "$CORRECTED_TRAJ"
        fi
        
    fi
    
    echo "完成蛋白质 $protein_name 的处理"
    cd "$WORK_DIR"
done

echo "所有蛋白质处理完成"