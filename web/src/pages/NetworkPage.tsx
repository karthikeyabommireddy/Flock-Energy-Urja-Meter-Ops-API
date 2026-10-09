import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { Card, ErrorState, Loading, StaleBanner } from '../components/Ui';
import { formatNumber } from '../format';
import type { HierarchyIssue, HierarchyNode } from '../types';
import { usePageTitle } from '../usePageTitle';

/** How many issues of one kind are listed before "and N more". */
const ISSUES_SHOWN = 20;

/** The meter-list filter that selects the branch under a node of this type. */
const FILTER_FOR_LEVEL: Record<string, string> = {
  zone: 'zone',
  circle: 'circle',
  division: 'division',
  subdivision: 'subdivision',
  substation: 'substation',
  feeder: 'feeder',
  dt: 'dt',
};

function findNode(nodes: readonly HierarchyNode[], id: string): HierarchyNode | null {
  for (const node of nodes) {
    if (node.node_id === id) return node;
    const inside = findNode(node.children, id);
    if (inside) return inside;
  }
  return null;
}

/** The ids from the top down to `id`, or null if it is not in the tree. */
function pathTo(nodes: readonly HierarchyNode[], id: string): string[] | null {
  for (const node of nodes) {
    if (node.node_id === id) return [node.node_id];
    const inside = pathTo(node.children, id);
    if (inside) return [node.node_id, ...inside];
  }
  return null;
}

interface RowProps {
  node: HierarchyNode;
  depth: number;
  selected: string | null;
  open: ReadonlySet<string>;
  issues: ReadonlyMap<string, number>;
}

function TreeRow({ node, depth, selected, open, issues }: RowProps) {
  const problems = issues.get(node.node_id) ?? 0;
  const label = (
    <>
      <Link to={`/network?node=${encodeURIComponent(node.node_id)}`} aria-current={selected === node.node_id ? 'true' : undefined}>
        {node.name ?? node.node_id}
      </Link>
      <span className="meta">
        {node.node_id} · {formatNumber(node.meter_count ?? 0, 0)} meters
        {node.capacity_kva != null ? ` · ${formatNumber(node.capacity_kva, 0)} kVA` : ''}
      </span>
      {problems > 0 ? <span className="badge badge-warn">{problems} issue{problems === 1 ? '' : 's'}</span> : null}
    </>
  );
  if (node.children.length === 0) {
    return (
      <li>
        <div className="leaf" data-selected={selected === node.node_id}>
          {label}
        </div>
      </li>
    );
  }
  return (
    <li data-selected={selected === node.node_id}>
      <details open={open.has(node.node_id) || depth < 1}>
        <summary>{label}</summary>
        <ul>
          {node.children.map((child) => (
            <TreeRow key={child.node_id} node={child} depth={depth + 1} selected={selected} open={open} issues={issues} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export function NetworkPage() {
  usePageTitle('Network');
  const [params] = useSearchParams();
  const selectedId = params.get('node');
  const tree = useQuery({ queryKey: ['hierarchy'], queryFn: ({ signal }) => api.hierarchy(signal) });
  const report = useQuery({ queryKey: ['issues'], queryFn: ({ signal }) => api.issues(signal) });

  const issueList: HierarchyIssue[] = report.data?.data.issues ?? [];
  const issueCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const issue of issueList) counts.set(issue.id, (counts.get(issue.id) ?? 0) + 1);
    return counts;
  }, [issueList]);
  const byType = useMemo(() => {
    const groups = new Map<string, HierarchyIssue[]>();
    for (const issue of issueList) groups.set(issue.type, [...(groups.get(issue.type) ?? []), issue]);
    return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [issueList]);

  const roots = tree.data?.data ?? [];
  const open = useMemo(() => new Set(selectedId ? (pathTo(roots, selectedId) ?? []) : []), [roots, selectedId]);
  const selected = selectedId ? findNode(roots, selectedId) : null;
  const known = useMemo(() => new Set(issueList.flatMap((i) => (findNode(roots, i.id) ? [i.id] : []))), [issueList, roots]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Network</h1>
          <p>Zone, circle, division, subdivision, substation, feeder and transformer, rebuilt from the meters' own breadcrumbs.</p>
        </div>
      </div>
      <StaleBanner caches={[tree.data?.cache, report.data?.cache]} />

      {tree.isPending ? <Loading label="Reading the network. The first read visits one meter per transformer, so it takes a moment." /> : null}
      {tree.isError ? <ErrorState error={tree.error} onRetry={() => void tree.refetch()} /> : null}

      {tree.isSuccess ? (
        <div className="layout-split">
          <Card title="Hierarchy">
            <ul className="tree">
              {roots.map((node) => (
                <TreeRow key={node.node_id} node={node} depth={0} selected={selectedId} open={open} issues={issueCounts} />
              ))}
            </ul>
          </Card>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Card title={selected ? (selected.name ?? selected.node_id) : 'Select a node'}>
              {selected ? (
                <>
                  <dl className="facts">
                    <dt>Level</dt>
                    <dd>{selected.node_type ?? '—'}</dd>
                    <dt>Code</dt>
                    <dd>{selected.node_id}</dd>
                    <dt>Meters</dt>
                    <dd>{formatNumber(selected.meter_count ?? 0, 0)}</dd>
                    {selected.capacity_kva != null ? (
                      <>
                        <dt>Capacity</dt>
                        <dd>{formatNumber(selected.capacity_kva, 0)} kVA</dd>
                      </>
                    ) : null}
                  </dl>
                  {selected.node_type && FILTER_FOR_LEVEL[selected.node_type] ? (
                    <Link className="button button-primary" to={`/meters?${FILTER_FOR_LEVEL[selected.node_type]}=${encodeURIComponent(selected.node_id)}`}>
                      See its meters
                    </Link>
                  ) : null}
                </>
              ) : (
                <p>{selectedId ? `No node ${selectedId} in the tree.` : 'Pick a node in the tree to see what hangs off it.'}</p>
              )}
            </Card>

            <Card title="Data quality">
              {report.isPending ? <Loading label="Checking…" /> : null}
              {report.isError ? <ErrorState error={report.error} onRetry={() => void report.refetch()} /> : null}
              {report.isSuccess && issueList.length === 0 ? <p>Nothing to report: the paths agree with each other and with the transformer list.</p> : null}
              {byType.map(([type, items]) => (
                <details key={type}>
                  <summary>
                    <strong>{type.replaceAll('_', ' ')}</strong> · {items.length}
                  </summary>
                  <ul className="anomalies">
                    {items.slice(0, ISSUES_SHOWN).map((issue) => (
                      <li key={`${issue.type}:${issue.id}`} data-severity="warn">
                        <strong>{known.has(issue.id) ? <Link to={`/network?node=${encodeURIComponent(issue.id)}`}>{issue.id}</Link> : issue.id}</strong>
                        <span>{issue.detail}</span>
                      </li>
                    ))}
                  </ul>
                  {items.length > ISSUES_SHOWN ? <p className="note">and {items.length - ISSUES_SHOWN} more of this kind.</p> : null}
                </details>
              ))}
            </Card>
          </div>
        </div>
      ) : null}
    </>
  );
}
