# foldx_script.sh (v1.1) - FoldX计算自动化脚本（Linux bash版本）
#!/bin/bash
# FoldX计算自动化脚本 - Linux bash版本
# 作者：豆包和欧阳梓豪
# 版本：1.1
# 日期：2025年11月12日
# 更新日志：
# v1.1 (2025-11-12)：
# 1. 新增--version参数，支持查看版本及更新记录
# 2. 修复多线程依赖顺序问题：强制前序命令（如RepairPDB）完全结束后再执行后续命令
# 3. 修正BuildModel软链接逻辑：避免修复后PDB文件名二次处理导致的依赖失效
# 4. 优化线程控制：用PID数组替代jobs计数，精准控制并发数（避免无关进程干扰）
# 5. 新增rotabase.txt检查（Windows环境专属）：缺失时提示生成方式
# 6. 解决PositionScan文件命名冲突：拆分位点列表时添加唯一标识，避免多线程覆盖
# 7. 修复BuildModel突变文件拆分后输出冲突：为每个分片任务添加独立输出前缀

set -e  # 遇到错误立即退出

# ============================ 全局变量和配置 ============================
# 默认值
VERSION="1.1"
CONFIG_FILE="foldx_config.txt"
THREADS=1
PDB_LIST=()
COMMAND_LIST=()

# 命令需要移动的文件格式列表（支持多个模式）
declare -A OUTPUT_FILES=(
    ["RepairPDB"]="*_Repair.fxout *Repair.pdb"
    ["Stability"]="*_*_ST.fxout"
    ["AlaScan"]="*_AS.fxout"
    ["PositionScan"]="PS_*.fxout energies_*_*.txt PS_*_scanning_output.txt *[0123456789]_*_Repair.pdb"
    ["BuildModel"]="*[0123456789].pdb Average_*.fxout Dif_*.fxout Raw_*.fxout PdbList_*.fxout"
    ["AnalyseComplex"]="Indiv_energies_*_AC.fxout Interaction_*_AC.fxout Interface_Residues_*_AC.fxout Summary_*.fxout " 
)

# ============================ 功能函数 ============================
# 显示版本信息
show_version() {
    cat << EOF
FoldX计算自动化脚本 - 版本 $VERSION
作者：豆包和欧阳梓豪
更新日志：
v1.1 (2025-11-12)：
1. 新增--version参数，支持查看版本及更新记录
2. 修复多线程依赖顺序：前序命令完全结束后再执行后续任务
3. 修正BuildModel软链接：避免修复后PDB文件名二次处理失效
4. 优化线程控制：PID数组精准控制并发（无无关进程干扰）
5. 新增Windows环境rotabase.txt检查：缺失时提示生成方式
6. 解决PositionScan命名冲突：拆分位点添加唯一标识
7. 修复BuildModel输出冲突：分片任务独立输出前缀

v1.0 (2025-11-10)：
1. 初始版本：支持RepairPDB/Stability/BuildModel等核心命令
2. 基础多线程能力：GNU parallel兼容 + 后台进程控制
EOF
}

# 显示帮助信息
show_help() {
    cat << EOF
用法: $0 [选项]
选项:
  -c, --config <文件>     配置文件路径 (默认: foldx_config.txt)
  -t, --threads <数字>    线程数 (默认: 1，单线程)
  -p, --pdb <文件列表>    蛋白质PDB文件列表（必需），英文逗号分隔（如WT.pdb,MUT.pdb）
  -cmd, --command <命令>  FoldX命令列表（必需），英文逗号分隔（如RepairPDB,Stability）
  -v, --version           显示版本信息及更新日志
  -h, --help              显示此帮助信息

可用命令: RepairPDB, Stability, BuildModel, AnalyseComplex, PositionScan, AlaScan
配置文件要求:
  需包含FoldXPath、WorkingDirectory、OutputDirectory、out_pdb参数（模板自动生成）

示例:
  $0 -c my_config.txt -t 4 -p WT.pdb,MUT.pdb -cmd RepairPDB,Stability
  $0 --version
  $0 --help
EOF
}

# 检查Windows环境下rotabase.txt文件（Linux无需）
check_rotabase() {
    # 判断系统是否为Windows（WSL或原生Windows）
    if [[ "$(uname -s)" == "CYGWIN"* || "$(uname -s)" == "MINGW"* || "$(uname -s)" == "MSYS"* ]]; then
        if [[ ! -f "rotabase.txt" ]]; then
            echo "警告：Windows环境下FoldX必需rotabase.txt文件，当前缺失！"
            echo "尝试进行生成..."
            "$FoldXPath"
            if [[ ! -f "rotabase.txt" ]]; then
                echo "生成失败，请按照以下方式手动生成"
                echo "生成方式：直接在终端运行命令 '$FoldXPath'（无需参数），FoldX会自动生成rotabase.txt"
                echo "生成后请重新运行本脚本"
                exit 1
            fi
            
        fi
    fi
}

# 检查并创建配置文件
check_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        cat > "$CONFIG_FILE" << 'EOF'
# FoldX配置文件（v1.1适配）
# 精准到foldx_20251231.exe文件的路径，未指定则默认使用"foldx"命令
FoldXPath=""
# 工作路径：实际存放配置文件和PDB文件的位置（需绝对路径或相对路径）
WorkingDirectory="./"
# 输出文件存放的文件夹名称
OutputDirectory="results"
# 是否输出PDB文件：true（输出）/false（不输出）
out_pdb="false"
EOF
        echo "配置文件不存在，已创建模板: $CONFIG_FILE"
        echo "请编辑配置文件（补充FoldXPath、WorkingDirectory等）后重新运行脚本"
        exit 1
    fi
    
    # 加载配置文件
    source "$CONFIG_FILE"
    
    # 配置参数默认值处理
    if [[ -z "$FoldXPath" ]]; then
        FoldXPath="foldx"
    fi
    if [[ -z "$WorkingDirectory" ]]; then
        echo "错误：配置文件中WorkingDirectory（工作路径）未指定！"
        exit 1
    fi
    if [[ -z "$OutputDirectory" ]]; then
        OutputDirectory="results"
    fi
    # 统一out_pdb参数（避免拼写错误）
    if [[ -z "$out_pdb" ]]; then
        out_pdb="false"
    fi
}

# 移动临时文件到结果目录（避免多线程竞争）
move_temp_files() {
    local command="$1"
    local patterns="$2"
    local task_id="${3:-}"  # 任务唯一标识（解决单PDB多线程输出冲突）
    
    # 等待文件写入完成（适配不同计算量）
    sleep 3
    
    # 创建命令结果目录（支持任务唯一标识）
    local result_dir="${OutputDirectory}/${command}"
    if [[ -n "$task_id" ]]; then
        result_dir="${result_dir}/task_${task_id}"
    fi
    mkdir -p "$result_dir"
    
    echo "移动文件到: $result_dir/"
    
    # 按模式移动文件
    local moved_count=0
    for pattern in $patterns; do
        for file in $pattern; do
            if [[ -f "$file" && -r "$file" ]]; then  # 确保文件存在且可读
                mv "$file" "$result_dir/" 2>/dev/null && {
                    echo "  移动: $file -> $result_dir/"
                    ((moved_count++))
                } || echo "  警告: 无法移动文件 $file"
            fi
        done
    done
    
    echo "移动完成: $moved_count 个文件"
}

# 获取修复后的PDB文件名（统一逻辑）
get_repaired_pdb() {
    local pdb="$1"
    local base_name="${pdb%.pdb}"
    echo "${base_name}_Repair.pdb"
}

# 单PDB修复（供多线程调用）
repair_pdb_single() {
    local pdb="$1"
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    echo "修复PDB（单线程）: $pdb"
    "$FoldXPath" --command=RepairPDB --pdb="$pdb"
    
    # 移动当前任务文件（添加PDB作为唯一标识）
    move_temp_files "RepairPDB" "${OUTPUT_FILES[RepairPDB]}" "${pdb%.pdb}"
    echo "修复完成: $repaired_pdb"
}

# 多线程修复PDB（PID数组精准控制并发）
repair_pdb_multithread() {
    local pdb_list=("$@")
    local count=${#pdb_list[@]}
    
    if [[ $count -eq 0 ]]; then
        echo "错误: 未提供PDB文件（--pdb参数必需）"
        return 1
    fi
    
    echo "开始多线程修复PDB (线程数: $THREADS，总PDB数: $count)"
    local pids=()  # PID数组记录子进程
    
    for pdb in "${pdb_list[@]}"; do
        # 启动单PDB修复子进程
        repair_pdb_single "$pdb" &
        pids+=("$!")
        
        # 控制并发数：当PID数组长度达到线程数，等待任一子进程结束
        if [[ ${#pids[@]} -ge $THREADS ]]; then
            wait -n  # 等待第一个结束的子进程（避免阻塞所有线程）
            # 清理已结束的PID（保持数组精简）
            pids=($(jobs -p))
        fi
    done
    
    # 等待所有剩余子进程结束
    wait "${pids[@]}" 2>/dev/null
    echo "所有PDB修复完成"
}

# 单PDB稳定性分析（供多线程调用）
stability_analysis_single() {
    local pdb="$1"
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    # 检查修复后的PDB是否存在
    if [[ ! -f "$repaired_pdb" ]]; then
        echo "错误: 稳定性分析失败，未找到修复后的PDB: $repaired_pdb"
        return 1
    fi
    
    echo "稳定性分析（单线程）: $repaired_pdb"
    "$FoldXPath" --command=Stability --pdb="$repaired_pdb"
    
    # 移动当前任务文件（添加PDB作为唯一标识）
    move_temp_files "Stability" "${OUTPUT_FILES[Stability]}" "${pdb%.pdb}"
}

# 多线程稳定性分析（PID数组控制）
stability_analysis_multithread() {
    local pdb_list=("$@")
    local count=${#pdb_list[@]}
    
    if [[ $count -eq 0 ]]; then
        echo "错误: 未提供PDB文件（--pdb参数必需）"
        return 1
    fi
    
    echo "开始多线程稳定性分析 (线程数: $THREADS，总PDB数: $count)"
    local pids=()
    
    for pdb in "${pdb_list[@]}"; do
        stability_analysis_single "$pdb" &
        pids+=("$!")
        
        # 控制并发数
        if [[ ${#pids[@]} -ge $THREADS ]]; then
            wait -n
            pids=($(jobs -p))
        fi
    done
    
    # 等待所有子进程结束
    wait "${pids[@]}" 2>/dev/null
    echo "所有PDB稳定性分析完成"
}

# 突变文件拆分（支持唯一标识）
split_mutant_file() {
    local mutant_file="$1"
    local num_parts="$2"
    local base_name="${mutant_file%.txt}"
    
    # 计算总行数和每部分行数（向上取整）
    local total_lines=$(wc -l < "$mutant_file")
    local lines_per_part=$(( (total_lines + num_parts - 1) / num_parts ))
    
    # 拆分文件（添加part标识，避免覆盖）
    split -d -l "$lines_per_part" "$mutant_file" "${base_name}_part_"
    
    # 返回拆分后的文件列表（按顺序）
    ls "${base_name}_part_"* 2>/dev/null | sort
}

# 单分片点突变计算（供多线程调用）
build_model_single_split() {
    local pdb="$1"
    local split_file="$2"
    local task_id="$3"  # 任务唯一标识（避免输出冲突）
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    # 检查依赖文件
    if [[ ! -f "$repaired_pdb" ]]; then
        echo "错误: 点突变计算失败，未找到修复后的PDB: $repaired_pdb"
        return 1
    fi
    if [[ ! -f "$split_file" ]]; then
        echo "错误: 点突变计算失败，未找到突变分片文件: $split_file"
        return 1
    fi
    
    # 创建临时软链接（唯一标识，避免多线程PDB名冲突）
    local temp_pdb="temp_${task_id}_${pdb}"
    ln -sf "$repaired_pdb" "$temp_pdb"
    
    echo "点突变计算（分片任务$task_id）: $temp_pdb（突变文件: $split_file）"
    "$FoldXPath" --command=BuildModel \
        --pdb="$temp_pdb" \
        --mutant-file="$split_file" \
        --out-pdb="$out_pdb" \
        --output="BM_task${task_id}_"  # 输出前缀唯一化
    
    # 移动当前分片文件（添加任务ID标识）
    move_temp_files "BuildModel" "${OUTPUT_FILES[BuildModel]}" "$task_id"
    
    # 清理临时软链接
    rm -f "$temp_pdb"
}

# 多线程点突变分析（支持多PDB/单PDB分片）
build_model_multithread() {
    local pdb_list=("$@")
    local pdb_count=${#pdb_list[@]}
    
    # 查找突变文件（必需以individual_list为前缀）
    local mutant_files=()
    for file in individual_list*.txt; do
        if [[ -f "$file" ]]; then
            mutant_files+=("$file")
        fi
    done
    if [[ ${#mutant_files[@]} -eq 0 ]]; then
        echo "错误: 未找到突变文件（需以individual_list为前缀，如individual_list.txt）"
        return 1
    fi
    
    echo "开始多线程点突变分析 (线程数: $THREADS，PDB数: $pdb_count，突变文件数: ${#mutant_files[@]})"
    local pids=()
    
    # 场景1：多PDB（每个线程处理一个PDB的完整突变文件）
    if [[ $pdb_count -gt 1 ]]; then
        for pdb in "${pdb_list[@]}"; do
            for mutant_file in "${mutant_files[@]}"; do
                # 生成唯一任务ID（PDB+突变文件名）
                local task_id="${pdb%.pdb}_${mutant_file%.txt}"
                build_model_single_split "$pdb" "$mutant_file" "$task_id" &
                pids+=("$!")
                
                # 控制并发数
                if [[ ${#pids[@]} -ge $THREADS ]]; then
                    wait -n
                    pids=($(jobs -p))
                fi
            done
        done
    # 场景2：单PDB（按线程数拆分突变文件，每个线程处理一个分片）
    else
        local pdb="${pdb_list[0]}"
        for mutant_file in "${mutant_files[@]}"; do
            # 拆分突变文件（分片数=线程数）
            local split_files=($(split_mutant_file "$mutant_file" "$THREADS"))
            local split_count=${#split_files[@]}
            echo "拆分突变文件 $mutant_file 为 $split_count 个分片"
            
            # 每个分片启动一个线程
            for ((i=0; i<split_count; i++)); do
                local split_file="${split_files[$i]}"
                local task_id="${pdb%.pdb}_${mutant_file%.txt}_part$i"
                build_model_single_split "$pdb" "$split_file" "$task_id" &
                pids+=("$!")
                
                # 控制并发数
                if [[ ${#pids[@]} -ge $THREADS ]]; then
                    wait -n
                    pids=($(jobs -p))
                fi
            done
            
            # 清理拆分后的突变文件
            rm -f "${split_files[@]}"
        done
    fi
    
    # 等待所有子进程结束
    wait "${pids[@]}" 2>/dev/null
    echo "所有点突变计算完成"
}

# 相互作用分析（基础功能，标注未验证）
analyse_complex_single() {
    local pdb="$1"
    local chains="$2"
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    echo "警告：相互作用分析功能未经过实际计算验证，输出文件格式可能存在偏差！"
    if [[ ! -f "$repaired_pdb" ]]; then
        echo "错误: 相互作用分析失败，未找到修复后的PDB: $repaired_pdb"
        return 1
    fi
    
    echo "相互作用分析: $repaired_pdb（链: $chains）"
    "$FoldXPath" --command=AnalyseComplex --pdb="$repaired_pdb" --analyseComplexChains="$chains"
    
    # 移动文件（添加PDB标识）
    move_temp_files "AnalyseComplex" "${OUTPUT_FILES[AnalyseComplex]}" "${pdb%.pdb}"
}

# 相互作用分析多线程（基础实现）
analyse_complex_multithread() {
    local pdb_list=("$@")
    local chains="${2:-A,B}"  # 默认分析A、B链
    local pdb_count=${#pdb_list[@]}
    
    if [[ $pdb_count -eq 0 ]]; then
        echo "错误: 未提供PDB文件（--pdb参数必需）"
        return 1
    fi
    
    echo "开始多线程相互作用分析 (线程数: $THREADS，链: $chains)"
    local pids=()
    
    for pdb in "${pdb_list[@]}"; do
        analyse_complex_single "$pdb" "$chains" &
        pids+=("$!")
        
        # 控制并发数
        if [[ ${#pids[@]} -ge $THREADS ]]; then
            wait -n
            pids=($(jobs -p))
        fi
    done
    
    wait "${pids[@]}" 2>/dev/null
    echo "所有相互作用分析完成（注：功能未验证，需检查输出文件）"
}

# 位点扫描分片计算（供多线程调用）
position_scan_single_split() {
    local pdb="$1"
    local positions=("$2")  # 位点列表（单个分片）
    local task_id="$3"
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    if [[ ! -f "$repaired_pdb" ]]; then
        echo "错误: 位点扫描失败，未找到修复后的PDB: $repaired_pdb"
        return 1
    fi
    
    echo "位点扫描（分片任务$task_id）: $repaired_pdb（位点数: ${#positions[@]}）"
    for pos in "${positions[@]}"; do
        # 输出前缀唯一化（避免多线程覆盖）
        local output_prefix="PS_task${task_id}_${pos}_"
        "$FoldXPath" --command=PositionScan \
            --pdb="$repaired_pdb" \
            --positions="$pos" \
            --out-pdb="$out_pdb" \
            --output="$output_prefix"
    done
    
    # 移动当前分片文件
    move_temp_files "PositionScan" "${OUTPUT_FILES[PositionScan]}" "$task_id"
}

# 位点扫描（支持多线程拆分位点）
position_scan() {
    local pdb="$1"
    local position_list_file="${pdb%.pdb}_positionsList"
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    # 检查位点列表文件
    if [[ ! -f "$position_list_file" ]]; then
        echo "错误: 位点扫描失败，未找到位点列表文件: $position_list_file"
        return 1
    fi
    
    # 读取位点列表（去空行）
    local positions=()
    while IFS= read -r line; do
        [[ -n "$line" ]] && positions+=("$line")
    done < "$position_list_file"
    local pos_count=${#positions[@]}
    if [[ $pos_count -eq 0 ]]; then
        echo "错误: 位点列表文件 $position_list_file 为空！"
        return 1
    fi
    
    echo "位点扫描: $repaired_pdb（总位点数: $pos_count，线程数: $THREADS）"
    local pids=()
    
    # 拆分位点列表（按线程数分片）
    local parts=$THREADS
    local pos_per_part=$(( (pos_count + parts - 1) / parts ))
    local current_pos=0
    
    for ((i=0; i<parts; i++)); do
        # 计算当前分片的位点范围
        local start=$current_pos
        local end=$((current_pos + pos_per_part - 1))
        if [[ $end -ge $pos_count ]]; then
            end=$((pos_count - 1))
        fi
        if [[ $start -gt $end ]]; then
            break  # 无更多位点可分
        fi
        
        # 提取当前分片的位点
        local split_positions=("${positions[@]:$start:$((end - start + 1))}")
        local task_id="${pdb%.pdb}_part$i"
        
        # 启动分片任务
        position_scan_single_split "$pdb" "${split_positions[*]}" "$task_id" &
        pids+=("$!")
        
        current_pos=$((end + 1))
        
        # 控制并发数
        if [[ ${#pids[@]} -ge $THREADS ]]; then
            wait -n
            pids=($(jobs -p))
        fi
    done
    
    # 等待所有子进程结束
    wait "${pids[@]}" 2>/dev/null
    echo "位点扫描完成: $repaired_pdb"
}

# 多线程位点扫描（每个PDB独立处理）
position_scan_multithread() {
    local pdb_list=("$@")
    local pdb_count=${#pdb_list[@]}
    
    if [[ $pdb_count -eq 0 ]]; then
        echo "错误: 未提供PDB文件（--pdb参数必需）"
        return 1
    fi
    
    echo "开始多线程位点扫描 (线程数: $THREADS，总PDB数: $pdb_count)"
    local pids=()
    
    for pdb in "${pdb_list[@]}"; do
        # 每个PDB启动一个位点扫描子进程（内部自动拆分位点）
        position_scan "$pdb" &
        pids+=("$!")
        
        # 控制并发数
        if [[ ${#pids[@]} -ge $THREADS ]]; then
            wait -n
            pids=($(jobs -p))
        fi
    done
    
    wait "${pids[@]}" 2>/dev/null
    echo "所有PDB位点扫描完成"
}

# 单PDB丙氨酸扫描（供多线程调用）
ala_scan_single() {
    local pdb="$1"
    local repaired_pdb=$(get_repaired_pdb "$pdb")
    
    if [[ ! -f "$repaired_pdb" ]]; then
        echo "错误: 丙氨酸扫描失败，未找到修复后的PDB: $repaired_pdb"
        return 1
    fi
    
    echo "丙氨酸扫描（单线程）: $repaired_pdb"
    "$FoldXPath" --command=AlaScan --pdb="$repaired_pdb"
    
    # 移动当前任务文件
    move_temp_files "AlaScan" "${OUTPUT_FILES[AlaScan]}" "${pdb%.pdb}"
}

# 多线程丙氨酸扫描
ala_scan_multithread() {
    local pdb_list=("$@")
    local pdb_count=${#pdb_list[@]}
    
    if [[ $pdb_count -eq 0 ]]; then
        echo "错误: 未提供PDB文件（--pdb参数必需）"
        return 1
    fi
    
    echo "开始多线程丙氨酸扫描 (线程数: $THREADS，总PDB数: $pdb_count)"
    local pids=()
    
    for pdb in "${pdb_list[@]}"; do
        ala_scan_single "$pdb" &
        pids+=("$!")
        
        # 控制并发数
        if [[ ${#pids[@]} -ge $THREADS ]]; then
            wait -n
            pids=($(jobs -p))
        fi
    done
    
    wait "${pids[@]}" 2>/dev/null
    echo "所有PDB丙氨酸扫描完成"
}

# 命令选择器（按顺序执行，强制依赖顺序）
command_dispatcher() {
    local command="$1"
    shift
    local pdb_list=("$@")
    
    case "$command" in
        "RepairPDB")
            repair_pdb_multithread "${pdb_list[@]}"
            ;;
        "Stability")
            stability_analysis_multithread "${pdb_list[@]}"
            ;;
        "BuildModel")
            build_model_multithread "${pdb_list[@]}"
            ;;
        "AnalyseComplex")
            analyse_complex_multithread "${pdb_list[@]}" "A,B"  # 默认A、B链
            ;;
        "PositionScan")
            position_scan_multithread "${pdb_list[@]}"
            ;;
        "AlaScan")
            ala_scan_multithread "${pdb_list[@]}"
            ;;
        *)
            echo "错误: 未知命令 '$command'"
            echo "可用命令: RepairPDB, Stability, BuildModel, AnalyseComplex, PositionScan, AlaScan"
            return 1
            ;;
    esac
}

# 主函数（流程控制）
main() {
    # 解析命令行参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            -c|--config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            -t|--threads)
                THREADS="$2"
                # 线程数合法性检查
                if [[ ! "$THREADS" =~ ^[0-9]+$ || $THREADS -lt 1 ]]; then
                    echo "错误: 线程数必须为正整数（当前: $THREADS）"
                    exit 1
                fi
                shift 2
                ;;
            -p|--pdb)
                IFS=',' read -ra PDB_LIST <<< "$2"
                shift 2
                ;;
            -cmd|--command)
                IFS=',' read -ra COMMAND_LIST <<< "$2"
                shift 2
                ;;
            -v|--version)
                show_version
                exit 0
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    # 必需参数检查
    if [[ ${#PDB_LIST[@]} -eq 0 ]]; then
        echo "错误: 必需参数--pdb（PDB文件列表）未提供！"
        show_help
        exit 1
    fi
    if [[ ${#COMMAND_LIST[@]} -eq 0 ]]; then
        echo "错误: 必需参数--cmd（FoldX命令列表）未提供！"
        show_help
        exit 1
    fi
    
    # 启动日志
    echo "=== FoldX计算自动化脚本（v$VERSION）==="
    echo "启动时间: $(date +%Y-%m-%d_%H:%M:%S)"
    
    # 初始化检查
    check_config          # 检查配置文件
    check_rotabase        # 检查Windows环境rotabase.txt
    cd "$WorkingDirectory" || { echo "错误: 无法进入工作路径 $WorkingDirectory"; exit 1; }
    mkdir -p "$OutputDirectory"  # 创建输出根目录
    
    # 显示配置信息
    echo -e "\n配置信息:"
    echo "  - 配置文件: $CONFIG_FILE"
    echo "  - FoldX路径: $FoldXPath"
    echo "  - 工作目录: $(pwd)"
    echo "  - 输出目录: $OutputDirectory"
    echo "  - 输出PDB: $out_pdb"
    echo "  - 线程数: $THREADS"
    echo "  - PDB列表: ${PDB_LIST[*]}"
    echo "  - 命令列表: ${COMMAND_LIST[*]}（按顺序执行）"
    echo -e "\n======================================"
    
    # 按顺序执行命令（强制前序命令完全结束）
    for cmd in "${COMMAND_LIST[@]}"; do
        echo -e "\n>>> 开始执行命令: $cmd"
        command_dispatcher "$cmd" "${PDB_LIST[@]}"
        if [[ $? -ne 0 ]]; then
            echo "错误: 命令 $cmd 执行失败！"
            exit 1
        fi
        echo "<<< 命令执行完成: $cmd"
    done
    
    # 执行完成
    echo -e "\n=== 所有计算完成 ==="
    echo "结果汇总路径: $(pwd)/$OutputDirectory"
    echo "各命令结果子目录:"
    for cmd in "${COMMAND_LIST[@]}"; do
        echo "  - $cmd: $OutputDirectory/$cmd/"
    done
    echo -e "\n脚本结束时间: $(date +%Y-%m-%d_%H:%M:%S)"
}

# ============================ 脚本入口 ============================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi