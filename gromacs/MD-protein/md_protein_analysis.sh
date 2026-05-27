#!/bin/bash
# 纯蛋白MD数据分析入口脚本
# 使用方法: ./md_protein_analysis.sh <config_file> <analysis_type> [specific_protein]

set -euo pipefail

# ====================== 命令行参数处理 ======================
if [ $# -lt 2 ]; then
    echo "用法: $0 <配置文件> <分析类型> [特定蛋白质名称]"
    echo "可用分析类型:"
    echo "  rmsd       - 均方根偏差"
    echo "  rmsf       - 均方根波动"
    echo "  gyrate     - 回转半径"
    echo "  hbond      - 氢键分析"
    echo "  dssp       - 二级结构分析"
    echo "  outpdb     - 导出pdb文件"
    echo "  all        - 运行所有分析"
    echo ""
    echo "示例:"
    echo "  $0 config_protein.txt rmsd              # 分析所有蛋白质的RMSD"
    echo "  $0 config_protein.txt rmsd 1AKI         # 只分析1AKI的RMSD"
    exit 1
fi

CONFIG_FILE="$1"
ANALYSIS_TYPE="$2"
SPECIFIC_PROTEIN="${3:-}"

# ====================== 加载配置与函数库 ======================
# 检查配置文件
if [ ! -f "$CONFIG_FILE" ]; then
    echo "错误: 配置文件 $CONFIG_FILE 不存在"
    exit 1
fi
source "$CONFIG_FILE"

# 加载通用分析函数库
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
DATA_ANALYSIS_LIB="$SCRIPT_DIR/../tools/md_data_analysis.sh"
if [ ! -f "$DATA_ANALYSIS_LIB" ]; then
    echo "错误: 未找到分析函数库 $DATA_ANALYSIS_LIB"
    exit 1
fi
source "$DATA_ANALYSIS_LIB"

# ====================== 默认参数设置 ======================
SKIP_EXISTING="${SKIP_EXISTING:-true}"
ANALYSIS_ROOT_DIR="${ANALYSIS_DIR:-analysis}"
TPR_SUFFIX="${TPR_SUFFIX:-_md}"
TRAJ_SUFFIX="${TRAJ_SUFFIX:-_md_corrected}"
GRO_SUFFIX="${GRO_SUFFIX:-_md_corrected}"
MD_PRODUCTION_DIR="${MD_PRODUCTION_DIR:-08_md_production}"
TRAJ_CORRECTION_DIR="${TRAJ_CORRECTION_DIR:-09_traj_correction}"

# ====================== 必要参数检查 ======================
check_required_param() {
    if [ -z "${!1:-}" ]; then
        echo "错误: 配置文件中缺少必要参数: $1"
        exit 1
    fi
}

required_params=("PROTEIN_LIST" "WORK_DIR")
for param in "${required_params[@]}"; do
    check_required_param "$param"
done

# ====================== 解析蛋白质列表 ======================
IFS=',' read -ra PROTEINS <<< "$PROTEIN_LIST"

# 过滤特定蛋白质
if [ -n "$SPECIFIC_PROTEIN" ]; then
    FILTERED_PROTEINS=()
    for PROTEIN in "${PROTEINS[@]}"; do
        PROTEIN=$(echo "$PROTEIN" | xargs)
        PROTEIN_NAME=$(basename "$PROTEIN" .pdb)
        if [ "$PROTEIN_NAME" = "$SPECIFIC_PROTEIN" ]; then
            FILTERED_PROTEINS=("$PROTEIN_NAME")
            break
        fi
    done
    if [ ${#FILTERED_PROTEINS[@]} -eq 0 ]; then
        echo "错误: 未找到蛋白质 $SPECIFIC_PROTEIN"
        exit 1
    fi
    PROTEINS=("${FILTERED_PROTEINS[@]}")
else
    # 转换为纯名称
    for i in "${!PROTEINS[@]}"; do
        PROTEINS[$i]=$(basename "${PROTEINS[$i]}" .pdb | xargs)
    done
fi

# ====================== 创建分析根目录 ======================
mkdir -p "$WORK_DIR/$ANALYSIS_ROOT_DIR"
cd "$WORK_DIR/$ANALYSIS_ROOT_DIR" || exit 1

# ====================== 执行分析 ======================
case "$ANALYSIS_TYPE" in
    "rmsd")
        check_required_param "RMSD_FIT_GROUP"
        check_required_param "RMSD_CAL_GROUP"
        
        ANALYSIS_DIR="rmsd"
        mkdir -p "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR" || exit 1
        
        for PROTEIN_NAME in "${PROTEINS[@]}"; do
            echo "===== 分析蛋白质: $PROTEIN_NAME ====="
            input_tpr="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$TPR_SUFFIX.tpr"
            input_traj="$WORK_DIR/$PROTEIN_NAME/$TRAJ_CORRECTION_DIR/$PROTEIN_NAME$TRAJ_SUFFIX.xtc"
            output_file="${PROTEIN_NAME}_rmsd.xvg"
            
            analyze_rmsd "$input_tpr" "$input_traj" "$output_file" "$RMSD_FIT_GROUP" "$RMSD_CAL_GROUP"
        done
        cd ..
        ;;
        
    "rmsf")
        check_required_param "RMSF_CAL_GROUP"
        
        ANALYSIS_DIR="rmsf"
        mkdir -p "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR" || exit 1
        
        for PROTEIN_NAME in "${PROTEINS[@]}"; do
            echo "===== 分析蛋白质: $PROTEIN_NAME ====="
            input_tpr="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$TPR_SUFFIX.tpr"
            input_traj="$WORK_DIR/$PROTEIN_NAME/$TRAJ_CORRECTION_DIR/$PROTEIN_NAME$TRAJ_SUFFIX.xtc"
            output_file="${PROTEIN_NAME}_rmsf.xvg"
            
            analyze_rmsf "$input_tpr" "$input_traj" "$output_file" "$RMSF_CAL_GROUP"
        done
        cd ..
        ;;
        
    "gyrate")
        check_required_param "GYRATE_CAL_GROUP"
        
        ANALYSIS_DIR="gyrate"
        mkdir -p "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR" || exit 1
        
        for PROTEIN_NAME in "${PROTEINS[@]}"; do
            echo "===== 分析蛋白质: $PROTEIN_NAME ====="
            input_tpr="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$TPR_SUFFIX.tpr"
            input_traj="$WORK_DIR/$PROTEIN_NAME/$TRAJ_CORRECTION_DIR/$PROTEIN_NAME$TRAJ_SUFFIX.xtc"
            output_file="${PROTEIN_NAME}_gyrate.xvg"
            
            analyze_gyrate "$input_tpr" "$input_traj" "$output_file" "$GYRATE_CAL_GROUP"
        done
        cd ..
        ;;
        
    "hbond")
        check_required_param "HBOND_REF_GROUP"
        check_required_param "HBOND_TARGET_GROUP"
        
        ANALYSIS_DIR="hbond"
        mkdir -p "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR" || exit 1
        
        for PROTEIN_NAME in "${PROTEINS[@]}"; do
            echo "===== 分析蛋白质: $PROTEIN_NAME ====="
            input_tpr="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$TPR_SUFFIX.tpr"
            input_traj="$WORK_DIR/$PROTEIN_NAME/$TRAJ_CORRECTION_DIR/$PROTEIN_NAME$TRAJ_SUFFIX.xtc"
            output_file="${PROTEIN_NAME}_hbnum.xvg"
            
            analyze_hbond "$input_tpr" "$input_traj" "$output_file" "$HBOND_REF_GROUP" "$HBOND_TARGET_GROUP"
        done
        cd ..
        ;;
        
    "dssp")
        check_required_param "DSSP_CAL_GROUP"
        
        ANALYSIS_DIR="dssp"
        mkdir -p "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR" || exit 1
        
        for PROTEIN_NAME in "${PROTEINS[@]}"; do
            echo "===== 分析蛋白质: $PROTEIN_NAME ====="
            input_tpr="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$TPR_SUFFIX.tpr"
            input_traj="$WORK_DIR/$PROTEIN_NAME/$TRAJ_CORRECTION_DIR/$PROTEIN_NAME$TRAJ_SUFFIX.xtc"
            output_dat="${PROTEIN_NAME}_dssp.dat"
            output_num="${PROTEIN_NAME}_dssp_num.xvg"
            
            analyze_dssp "$input_tpr" "$input_traj" "$output_dat" "$output_num" "$DSSP_CAL_GROUP"
        done
        cd ..
        ;;

    "outpdb")
        
        ANALYSIS_DIR="outpdb"
        mkdir -p "$ANALYSIS_DIR"
        cd "$ANALYSIS_DIR" || exit 1
        
        for SYSTEM_NAME in "${COMPLEXES[@]}"; do
            echo "===== 导出复合物: $SYSTEM_NAME ====="
            input_tpr="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$TPR_SUFFIX.tpr"
            input_gro="$WORK_DIR/$PROTEIN_NAME/$MD_PRODUCTION_DIR/$PROTEIN_NAME$GRO_SUFFIX.gro"
            output_pdb="${PROTEIN_NAME}_md_corrected.pdb"
            
            export_pdb "$input_tpr" "$input_gro" "$output_pdb" "$OUT_GROUP"
        done
        cd ..
        ;;
        
    "all")
        echo "===== 开始执行所有分析 ====="
        for analysis in rmsd rmsf gyrate hbond dssp; do
            echo ""
            echo "===== 执行分析: $analysis ====="
            "$0" "$CONFIG_FILE" "$analysis" "$SPECIFIC_PROTEIN"
        done
        ;;
        
    *)
        echo "错误: 未知的分析类型 '$ANALYSIS_TYPE'"
        echo "可用分析类型: rmsd, rmsf, gyrate, hbond, dssp, all"
        exit 1
        ;;
esac

echo ""
echo "[$(date +'%Y-%m-%d %H:%M:%S')] INFO: 分析完成: $ANALYSIS_TYPE"
echo "分析结果保存在: $WORK_DIR/$ANALYSIS_ROOT_DIR"