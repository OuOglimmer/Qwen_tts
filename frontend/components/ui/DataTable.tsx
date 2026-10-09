import type { ReactNode } from 'react'

export function DataTable({ headers, rows, rowKeys, rowClasses }: {
  headers: string[]
  rows: ReactNode[][]
  /** 稳定行 key（如任务 id）：行内含播放器时必须传，避免删除/重排后音频状态错位。 */
  rowKeys?: (string | number)[]
  rowClasses?: (string | undefined)[]
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={headers.length}>暂无数据</td></tr>}
          {rows.map((row, rowIndex) => (
            <tr key={rowKeys?.[rowIndex] ?? rowIndex} className={rowClasses?.[rowIndex]}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
