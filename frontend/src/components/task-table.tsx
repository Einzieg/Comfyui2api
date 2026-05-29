import type React from "react";
import { useMemo, useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type VisibilityState
} from "@tanstack/react-table";
import { ChevronLeft, ChevronRight, Columns3, Copy, Eye } from "lucide-react";
import type { TaskRecord } from "../lib/api";
import { compactId, formatDateTime, formatDuration } from "../lib/format";
import { ProgressCell } from "./progress-cell";
import { kindLabels, StatusBadge } from "./status-badge";

interface TaskTableProps {
  items: TaskRecord[];
  total: number;
  onOpenTask: (task: TaskRecord) => void;
}

export function TaskTable({ items, total, onOpenTask }: TaskTableProps): React.ReactElement {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});

  const columns = useMemo<ColumnDef<TaskRecord>[]>(
    () => [
      {
        accessorKey: "created_at_utc",
        header: "提交时间",
        cell: ({ row }) => <span className="two-line">{formatDateTime(row.original.created_at_utc)}</span>
      },
      {
        accessorKey: "finished_at_utc",
        header: "结束时间",
        cell: ({ row }) => <span>{formatDateTime(row.original.finished_at_utc)}</span>
      },
      {
        accessorKey: "duration_s",
        header: "耗时",
        cell: ({ row }) => <span className="duration-pill">{formatDuration(row.original.duration_s)}</span>
      },
      {
        accessorKey: "platform",
        header: "平台",
        cell: ({ row }) => <span className={`platform-pill platform-${row.original.platform.toLowerCase().replace(/[^a-z]/g, "")}`}>{row.original.platform}</span>
      },
      {
        accessorKey: "kind",
        header: "类型",
        cell: ({ row }) => <span className="kind-pill">{kindLabels[row.original.kind] ?? row.original.kind}</span>
      },
      {
        accessorKey: "job_id",
        header: "任务 ID",
        cell: ({ row }) => (
          <button
            className="id-button"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              void navigator.clipboard.writeText(row.original.job_id);
            }}
            title={row.original.job_id}
          >
            <span>{compactId(row.original.job_id, 26)}</span>
            <Copy size={14} />
          </button>
        )
      },
      {
        accessorKey: "status",
        header: "任务状态",
        cell: ({ row }) => <StatusBadge status={row.original.status} />
      },
      {
        accessorKey: "progress_percent",
        header: "进度",
        cell: ({ row }) => <ProgressCell value={row.original.progress_percent} status={row.original.status} />
      },
      {
        id: "detail",
        header: "详情",
        cell: ({ row }) => (
          <button
            className="detail-button"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onOpenTask(row.original);
            }}
          >
            <Eye size={15} />
            预览
          </button>
        )
      }
    ],
    [onOpenTask]
  );

  const table = useReactTable({
    data: items,
    columns,
    state: {
      sorting,
      columnVisibility
    },
    initialState: {
      pagination: { pageIndex: 0, pageSize: 50 }
    },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel()
  });

  const rows = table.getRowModel().rows;

  return (
    <section className="table-panel">
      <div className="panel-header">
        <div>
          <h2>任务队列与历史记录</h2>
          <p>WebSocket 实时推送，断线后自动轮询</p>
        </div>
        <details className="column-menu">
          <summary>
            <Columns3 size={16} />
            列设置
          </summary>
          <div>
            {table.getAllLeafColumns().map((column) => (
              <label key={column.id}>
                <input
                  type="checkbox"
                  checked={column.getIsVisible()}
                  onChange={column.getToggleVisibilityHandler()}
                />
                {String(column.columnDef.header ?? column.id)}
              </label>
            ))}
          </div>
        </details>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th key={header.id}>
                    {header.isPlaceholder ? null : (
                      <button type="button" onClick={header.column.getToggleSortingHandler()}>
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <span>{header.column.getIsSorted() === "asc" ? "↑" : header.column.getIsSorted() === "desc" ? "↓" : ""}</span>
                      </button>
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} onClick={() => onOpenTask(row.original)}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                ))}
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td className="empty-state" colSpan={columns.length}>
                  没有匹配的任务
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="pagination">
        <span>
          显示 {rows.length} / 共 {total} 条记录
        </span>
        <div>
          <button type="button" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} title="上一页">
            <ChevronLeft size={16} />
          </button>
          <strong>{table.getState().pagination.pageIndex + 1}</strong>
          <button type="button" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} title="下一页">
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </section>
  );
}
