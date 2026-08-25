import { BarChart3, FlaskConical, Info, Play, Settings2, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  ChartFrame,
  ChartTooltip,
  ComparisonTable,
  SERIES,
  axisProps,
  barProps,
  gridProps,
  lineProps,
} from '../components/charts'
import {
  Button,
  Callout,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Field,
  NumberInput,
  PageHeader,
  Pill,
  Spinner,
  StatTile,
  Table,
  Td,
  Th,
  cx,
  useToast,
} from '../components/ui'
import { api } from '../lib/api'
import { PERM, useAuth } from '../lib/auth'
import { compact, days, humanise, percent } from '../lib/format'
import { useAsync } from '../lib/useAsync'
import type { EvaluationRun } from '../lib/types'

const BASELINE_LABEL = 'Baseline — 90-day periodic review'
const ENGINE_LABEL = 'This engine — event-driven'

const TWO_SERIES = [
  { label: BASELINE_LABEL, color: SERIES.s2 },
  { label: ENGINE_LABEL, color: SERIES.s1 },
]

export function Evaluation() {
  const { user, can } = useAuth()
  const toast = useToast()

  const report = useAsync(() => api.latestEvaluation(), [])
  const [showConfig, setShowConfig] = useState(false)
  const [running, setRunning] = useState(false)

  const [form, setForm] = useState({
    num_customers: 1000,
    num_events: 5000,
    horizon_days: 365,
    baseline_review_interval_days: 90,
    random_seed: 42,
    fuzzy_match_threshold: 0, // 0 means "use the production default"
  })

  const run = async () => {
    setRunning(true)
    try {
      const result = await api.runEvaluation({
        ...form,
        fuzzy_match_threshold: form.fuzzy_match_threshold || null,
      })
      report.setData(result)
      toast.push('good', `Evaluation complete in ${result.runtime_seconds.toFixed(1)}s.`)
      setShowConfig(false)
    } catch (caught) {
      toast.push('critical', caught instanceof Error ? caught.message : 'Evaluation failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Baseline vs engine"
        subtitle="A labeled synthetic scenario where the ground truth is known by construction. Both policies see the identical event stream."
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              icon={<Settings2 className="size-3.5" />}
              onClick={() => setShowConfig((current) => !current)}
            >
              Parameters
            </Button>
            {can(PERM.runEvaluation) ? (
              <Button
                variant="primary"
                size="sm"
                icon={<Play className="size-3.5" />}
                loading={running}
                onClick={run}
              >
                Run evaluation
              </Button>
            ) : (
              <span className="text-[12px] text-muted">{user?.role} cannot run the harness</span>
            )}
          </>
        }
      />

      {showConfig && (
        <Card>
          <CardHeader
            title="Scenario parameters"
            subtitle="The seed fixes the scenario, so the same parameters always produce the same numbers."
          />
          <div className="mt-4 grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <Field label="Customers">
              <NumberInput
                value={form.num_customers}
                onChange={(value) => setForm({ ...form, num_customers: value })}
                min={50}
                max={5000}
              />
            </Field>
            <Field label="Events">
              <NumberInput
                value={form.num_events}
                onChange={(value) => setForm({ ...form, num_events: value })}
                min={100}
                max={25000}
              />
            </Field>
            <Field label="Horizon (days)">
              <NumberInput
                value={form.horizon_days}
                onChange={(value) => setForm({ ...form, horizon_days: value })}
                min={90}
                max={1095}
              />
            </Field>
            <Field label="Review cycle (days)">
              <NumberInput
                value={form.baseline_review_interval_days}
                onChange={(value) =>
                  setForm({ ...form, baseline_review_interval_days: value })
                }
                min={30}
                max={365}
              />
            </Field>
            <Field label="Random seed">
              <NumberInput
                value={form.random_seed}
                onChange={(value) => setForm({ ...form, random_seed: value })}
              />
            </Field>
            <Field label="Fuzzy threshold" hint="0 = production default">
              <NumberInput
                value={form.fuzzy_match_threshold}
                onChange={(value) => setForm({ ...form, fuzzy_match_threshold: value })}
                min={0}
                max={100}
              />
            </Field>
          </div>
        </Card>
      )}

      {report.loading && !report.data ? (
        <Spinner label="Loading the latest report…" />
      ) : report.error ? (
        report.error.includes('No evaluation') ? (
          <Card>
            <EmptyState
              icon={<FlaskConical className="size-6" />}
              title="No evaluation has been run yet"
              body={
                can(PERM.runEvaluation)
                  ? 'Run the harness to generate a labeled 5,000-event scenario and score both policies against it. Takes about 15 seconds.'
                  : 'Ask a Manager to run the harness. Running it requires the evaluation:run permission.'
              }
              action={
                can(PERM.runEvaluation) ? (
                  <Button
                    variant="primary"
                    icon={<Play className="size-3.5" />}
                    loading={running}
                    onClick={run}
                  >
                    Run evaluation
                  </Button>
                ) : undefined
              }
            />
          </Card>
        ) : (
          <ErrorState message={report.error} onRetry={report.reload} />
        )
      ) : report.data ? (
        <Report run={report.data} />
      ) : null}
    </div>
  )
}

function Report({ run }: { run: EvaluationRun }) {
  const { baseline, engine, entity_resolution, deltas, ground_truth } = run.metrics
  const charts = run.chart_data

  return (
    <div className="flex flex-col gap-6">
      {/* --------------------------------------------------- scenario summary */}
      <Card>
        <CardHeader
          title="Scenario"
          subtitle={`${compact(ground_truth.total_events)} events across ${compact(
            run.num_customers,
          )} customers over ${run.horizon_days} days. Seed ${run.random_seed}, so this is reproducible.`}
          actions={
            <span className="text-[11.5px] text-muted">
              Run #{run.id} · {run.runtime_seconds.toFixed(1)}s
            </span>
          }
        />
        <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-[12px]">
          <Fact label="Material by ground truth" value={compact(ground_truth.material_events)} />
          <Fact label="Immaterial" value={compact(ground_truth.immaterial_events)} />
          <Fact label="Material rate" value={percent(ground_truth.material_rate)} />
          <Fact
            label="Deliberate hard cases"
            value={compact(ground_truth.hard_case_events)}
          />
          <Fact
            label="Off-book decoy entities"
            value={compact(ground_truth.decoy_events)}
          />
          <Fact
            label="ER threshold"
            value={String(run.config.fuzzy_match_threshold ?? entity_resolution.operating_threshold)}
          />
        </div>
      </Card>

      {/* --------------------------------------------------------- headline */}
      <div>
        <h2 className="mb-3 text-[13px] font-semibold tracking-wider text-muted uppercase">
          Headline result
        </h2>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile
            label="Material event detection rate"
            value={percent(engine.detection_rate_recall)}
            delta={`${((deltas.detection_rate_gain ?? 0) * 100).toFixed(1)}pp vs baseline`}
            deltaGood={(deltas.detection_rate_gain ?? 0) > 0}
            tone="accent"
            footnote={`Baseline ${percent(baseline.detection_rate_recall)} · ${
              deltas.additional_material_events_caught
            } more events caught`}
          />
          <StatTile
            label="Median detection latency"
            value={days(engine.latency.median_days)}
            delta={`${deltas.median_latency_speedup_factor?.toLocaleString('en-GB')}× faster`}
            deltaGood
            tone="good"
            footnote={`Baseline ${days(baseline.latency.median_days)} · ${deltas.median_latency_reduction_days?.toFixed(
              0,
            )} days sooner`}
          />
          <StatTile
            label="False positive rate"
            value={percent(engine.false_positive_rate)}
            delta={`${((deltas.false_positive_rate_change ?? 0) * 100).toFixed(1)}pp vs baseline`}
            deltaGood={(deltas.false_positive_rate_change ?? 0) < 0}
            tone="warning"
            footnote={`Baseline ${percent(baseline.false_positive_rate)} · still the engine's weakest number`}
          />
          <StatTile
            label="Analyst touches"
            value={compact(engine.workload.analyst_touches)}
            delta={`${compact(deltas.analyst_touch_reduction)} fewer`}
            deltaGood
            tone="serious"
            footnote={`Baseline ${compact(baseline.workload.analyst_touches)} scheduled reviews, ${percent(
              baseline.workload.wasted_review_rate,
            )} of which found nothing`}
          />
        </div>
      </div>

      {/* ---------------------------------------------------- primary charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Events caught — the grouped bar the brief asked for */}
        <ChartFrame
          title="Events caught, missed, and falsely raised"
          subtitle="Counts over the same 5,000-event stream."
          series={TWO_SERIES}
          table={
            <ComparisonTable
              head={['Outcome', 'Baseline', 'Engine']}
              rows={charts.events_caught.map((row) => ({
                key: row.outcome,
                a: row.baseline,
                b: row.engine,
              }))}
              format={(value) => value.toLocaleString('en-GB')}
            />
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={charts.events_caught}
              margin={{ top: 8, right: 8, bottom: 4, left: -16 }}
              barGap={2}
              barCategoryGap="28%"
            >
              <CartesianGrid {...gridProps} />
              <XAxis dataKey="outcome" {...axisProps} />
              <YAxis {...axisProps} width={48} />
              <Tooltip
                cursor={{ fill: 'var(--surface-hover)' }}
                content={
                  <ChartTooltip formatter={(value: number) => value.toLocaleString('en-GB')} />
                }
              />
              <Bar dataKey="baseline" name={BASELINE_LABEL} fill={SERIES.s2} {...barProps} />
              <Bar dataKey="engine" name={ENGINE_LABEL} fill={SERIES.s1} {...barProps} />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>

        {/* Cumulative detection — the clearest single picture of the gap */}
        <ChartFrame
          title="Share of material events detected, by elapsed time"
          subtitle="The engine is effectively done inside a day; the baseline is still climbing at 90."
          series={TWO_SERIES}
          table={
            <ComparisonTable
              head={['Days elapsed', 'Baseline', 'Engine']}
              rows={charts.cumulative_detection.map((row) => ({
                key: `${row.days}d`,
                a: row.baseline,
                b: row.engine,
              }))}
              format={(value) => `${value.toFixed(1)}%`}
            />
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={charts.cumulative_detection}
              margin={{ top: 8, right: 20, bottom: 4, left: -16 }}
            >
              <CartesianGrid {...gridProps} />
              <XAxis
                dataKey="days"
                tickFormatter={(value) => `${value}d`}
                {...axisProps}
              />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(value) => `${value}%`}
                width={48}
                {...axisProps}
              />
              <Tooltip
                cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }}
                labelFormatter={(value) => `${value} days after the event`}
                content={
                  <ChartTooltip
                    labelFormatter={(value) => `${value} days after the event`}
                    formatter={(value: number) => `${value.toFixed(1)}%`}
                  />
                }
              />
              <Line
                type="monotone"
                dataKey="baseline"
                name={BASELINE_LABEL}
                {...lineProps(SERIES.s2)}
              />
              <Line
                type="monotone"
                dataKey="engine"
                name={ENGINE_LABEL}
                {...lineProps(SERIES.s1)}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>

        {/* Detection delay by category */}
        <ChartFrame
          title="Median detection delay by event category"
          subtitle="The gap holds across every category — it is not driven by one signal type."
          series={TWO_SERIES}
          table={
            <ComparisonTable
              head={['Category', 'Baseline (days)', 'Engine (days)']}
              rows={charts.delay_by_category.map((row) => ({
                key: row.category,
                a: row.baseline,
                b: row.engine,
              }))}
              format={(value) => value.toFixed(2)}
            />
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={charts.delay_by_category}
              layout="vertical"
              margin={{ top: 4, right: 40, bottom: 4, left: 8 }}
              barGap={2}
              barCategoryGap="26%"
            >
              <CartesianGrid {...gridProps} horizontal={false} vertical />
              <XAxis
                type="number"
                tickFormatter={(value) => `${value}d`}
                {...axisProps}
              />
              <YAxis
                type="category"
                dataKey="category"
                width={116}
                {...axisProps}
              />
              <Tooltip
                cursor={{ fill: 'var(--surface-hover)' }}
                content={
                  <ChartTooltip formatter={(value: number) => `${value.toFixed(2)} days`} />
                }
              />
              <Bar
                dataKey="baseline"
                name={BASELINE_LABEL}
                fill={SERIES.s2}
                maxBarSize={20}
                radius={[0, 4, 4, 0]}
              />
              <Bar
                dataKey="engine"
                name={ENGINE_LABEL}
                fill={SERIES.s1}
                maxBarSize={20}
                radius={[0, 4, 4, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>

        {/* Latency distribution */}
        <ChartFrame
          title="Detection latency distribution"
          subtitle="Where each policy's detections actually land."
          series={TWO_SERIES}
          table={
            <ComparisonTable
              head={['Latency bucket', 'Baseline', 'Engine']}
              rows={charts.latency_distribution.map((row) => ({
                key: row.bucket,
                a: row.baseline,
                b: row.engine,
              }))}
              format={(value) => value.toLocaleString('en-GB')}
            />
          }
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={charts.latency_distribution}
              margin={{ top: 8, right: 8, bottom: 4, left: -16 }}
              barGap={2}
              barCategoryGap="24%"
            >
              <CartesianGrid {...gridProps} />
              <XAxis
                dataKey="bucket"
                interval={0}
                angle={-18}
                textAnchor="end"
                height={52}
                {...axisProps}
              />
              <YAxis {...axisProps} width={48} />
              <Tooltip
                cursor={{ fill: 'var(--surface-hover)' }}
                content={
                  <ChartTooltip
                    formatter={(value: number) => `${value.toLocaleString('en-GB')} events`}
                  />
                }
              />
              <Bar dataKey="baseline" name={BASELINE_LABEL} fill={SERIES.s2} {...barProps} />
              <Bar dataKey="engine" name={ENGINE_LABEL} fill={SERIES.s1} {...barProps} />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      {/* -------------------------------------------------- entity resolution */}
      <div>
        <h2 className="mb-3 text-[13px] font-semibold tracking-wider text-muted uppercase">
          Entity resolution (Phase 1)
        </h2>

        <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile
            label="Precision"
            value={percent(entity_resolution.precision)}
            footnote={`${compact(entity_resolution.correct_matches)} correct of ${compact(
              entity_resolution.matches_attempted,
            )} matches made`}
          />
          <StatTile
            label="Recall"
            value={percent(entity_resolution.recall)}
            footnote={`${compact(entity_resolution.missed_matches)} resolvable events left unmatched`}
          />
          <StatTile
            label="F1"
            value={percent(entity_resolution.f1_score)}
            footnote={`At the operating threshold of ${entity_resolution.operating_threshold}`}
          />
          <StatTile
            label="Off-book decoys rejected"
            value={percent(entity_resolution.decoy_rejection_rate)}
            tone={
              (entity_resolution.decoy_rejection_rate ?? 0) < 0.5 ? 'critical' : 'good'
            }
            footnote={`${compact(
              entity_resolution.decoys_incorrectly_matched,
            )} entities not on the book were matched to a customer anyway`}
          />
        </div>

        {(entity_resolution.decoy_rejection_rate ?? 1) < 0.5 && (
          <div className="mb-3">
            <Callout tone="warning" title="Finding: the fuzzy threshold is too permissive">
              At the current operating threshold of{' '}
              <strong>{entity_resolution.operating_threshold}</strong>, the resolver
              accepts a customer match for{' '}
              <strong>
                {percent(1 - (entity_resolution.decoy_rejection_rate ?? 0), 0)}
              </strong>{' '}
              of entities that are not on the book at all. That is the single
              largest driver of the engine's false positives
              {engine.false_positive_causes?.MATCHED_ENTITY_NOT_ON_BOOK
                ? ` (${compact(
                    engine.false_positive_causes.MATCHED_ENTITY_NOT_ON_BOOK,
                  )} of ${compact(engine.false_positives)})`
                : ''}
              , and it also causes most of the engine's misses — a material event
              scored against the wrong customer is not a detection. The sweep below
              shows the whole trade-off curve. It is published rather than quietly
              tuned away, because picking the threshold that flatters this scenario
              and then reporting the result as validation would be fitting the
              system to its own test. Re-run with an explicit threshold to verify.
            </Callout>
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          <ChartFrame
            title="Fuzzy threshold sensitivity"
            subtitle="Precision, recall, and off-book rejection as the accept threshold moves. The marked point is the current setting."
            series={[
              { label: 'Precision', color: SERIES.s1 },
              { label: 'Recall', color: SERIES.s2 },
              { label: 'Decoy rejection', color: SERIES.s3 },
            ]}
            table={
              <Table>
                <thead>
                  <tr>
                    <Th>Threshold</Th>
                    <Th align="right">Precision</Th>
                    <Th align="right">Recall</Th>
                    <Th align="right">F1</Th>
                    <Th align="right">Wrong customer</Th>
                    <Th align="right">Unmatched</Th>
                    <Th align="right">Decoys matched</Th>
                  </tr>
                </thead>
                <tbody>
                  {entity_resolution.threshold_sensitivity.map((row) => (
                    <tr
                      key={row.fuzzy_threshold}
                      className={cx(row.is_operating_point && 'bg-sunken')}
                    >
                      <Td className="font-medium text-primary">
                        {row.fuzzy_threshold}
                        {row.is_operating_point && (
                          <Pill tone="accent" className="ml-1.5">
                            current
                          </Pill>
                        )}
                      </Td>
                      <Td align="right" className="tnum">
                        {percent(row.precision)}
                      </Td>
                      <Td align="right" className="tnum">
                        {percent(row.recall)}
                      </Td>
                      <Td align="right" className="tnum font-medium text-primary">
                        {percent(row.f1_score)}
                      </Td>
                      <Td align="right" className="tnum">
                        {row.wrong_customer_matches}
                      </Td>
                      <Td align="right" className="tnum">
                        {row.missed_matches}
                      </Td>
                      <Td align="right" className="tnum">
                        {row.decoys_incorrectly_matched}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            }
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={charts.entity_resolution_threshold_sweep}
                margin={{ top: 8, right: 12, bottom: 4, left: -16 }}
              >
                <CartesianGrid {...gridProps} />
                <XAxis dataKey="threshold" {...axisProps} />
                <YAxis
                  domain={[0, 100]}
                  tickFormatter={(value) => `${value}%`}
                  width={48}
                  {...axisProps}
                />
                <Tooltip
                  cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }}
                  content={
                    <ChartTooltip
                      labelFormatter={(value) => `Threshold ${value}`}
                      formatter={(value: number) => `${value.toFixed(1)}%`}
                    />
                  }
                />
                <Line
                  type="monotone"
                  dataKey="precision"
                  name="Precision"
                  {...lineProps(SERIES.s1)}
                />
                <Line
                  type="monotone"
                  dataKey="recall"
                  name="Recall"
                  {...lineProps(SERIES.s2)}
                />
                <Line
                  type="monotone"
                  dataKey="decoy_rejection"
                  name="Decoy rejection"
                  {...lineProps(SERIES.s3)}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>

          <ChartFrame
            title="Match accuracy by how the name arrived"
            subtitle="Exact names are easy. The interesting question is what happens to variants, typos, and off-book decoys."
            table={
              <Table>
                <thead>
                  <tr>
                    <Th>Name form</Th>
                    <Th align="right">Events</Th>
                    <Th align="right">Accuracy</Th>
                  </tr>
                </thead>
                <tbody>
                  {charts.entity_resolution_by_perturbation.map((row) => (
                    <tr key={row.perturbation}>
                      <Td className="text-primary">{row.perturbation}</Td>
                      <Td align="right" className="tnum">
                        {row.total}
                      </Td>
                      <Td align="right" className="tnum font-medium text-primary">
                        {row.accuracy.toFixed(1)}%
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            }
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={charts.entity_resolution_by_perturbation}
                layout="vertical"
                margin={{ top: 4, right: 44, bottom: 4, left: 8 }}
                barCategoryGap="26%"
              >
                <CartesianGrid {...gridProps} horizontal={false} vertical />
                <XAxis
                  type="number"
                  domain={[0, 100]}
                  tickFormatter={(value) => `${value}%`}
                  {...axisProps}
                />
                <YAxis type="category" dataKey="perturbation" width={132} {...axisProps} />
                <Tooltip
                  cursor={{ fill: 'var(--surface-hover)' }}
                  content={
                    <ChartTooltip formatter={(value: number) => `${value.toFixed(1)}%`} />
                  }
                />
                {/* Single series, so no legend. Colour is a magnitude cue: the
                    weakest name forms get the darkest step of the blue ramp. */}
                <Bar dataKey="accuracy" name="Match accuracy" maxBarSize={20} radius={[0, 4, 4, 0]}>
                  {charts.entity_resolution_by_perturbation.map((row) => (
                    <Cell
                      key={row.perturbation}
                      fill={
                        row.accuracy >= 95
                          ? 'var(--seq-250)'
                          : row.accuracy >= 80
                            ? 'var(--seq-400)'
                            : 'var(--seq-550)'
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        </div>
      </div>

      {/* --------------------------------------------------- full metric table */}
      <Card padded={false}>
        <div className="p-5 pb-0">
          <CardHeader
            title="Every metric, side by side"
            subtitle="The table view for the whole report — nothing above is reachable only through a chart."
          />
        </div>
        <div className="mt-4">
          <Table>
            <thead>
              <tr>
                <Th>Metric</Th>
                <Th align="right">Baseline</Th>
                <Th align="right">Engine</Th>
                <Th>Note</Th>
              </tr>
            </thead>
            <tbody>
              <MetricRow
                label="Material event detection rate (recall)"
                a={percent(baseline.detection_rate_recall)}
                b={percent(engine.detection_rate_recall)}
                better="b"
                note="Share of ground-truth material events that triggered a reassessment"
              />
              <MetricRow
                label="False positive rate"
                a={percent(baseline.false_positive_rate)}
                b={percent(engine.false_positive_rate)}
                better="b"
                note="Share of immaterial events that triggered a reassessment"
              />
              <MetricRow
                label="Precision"
                a={percent(baseline.precision)}
                b={percent(engine.precision)}
                better="b"
                note="Of everything each policy raised, how much was genuinely material"
              />
              <MetricRow
                label="F1"
                a={percent(baseline.f1_score)}
                b={percent(engine.f1_score)}
                better="b"
              />
              <MetricRow
                label="Median detection latency"
                a={days(baseline.latency.median_days)}
                b={days(engine.latency.median_days)}
                better="b"
                note="Event occurred → reassessment triggered"
              />
              <MetricRow
                label="Mean detection latency"
                a={days(baseline.latency.mean_days)}
                b={days(engine.latency.mean_days)}
                better="b"
              />
              <MetricRow
                label="90th percentile latency"
                a={days(baseline.latency.p90_days)}
                b={days(engine.latency.p90_days)}
                better="b"
                note="The tail is what an enforcement notice describes"
              />
              <MetricRow
                label="Worst-case latency"
                a={days(baseline.latency.max_days)}
                b={days(engine.latency.max_days)}
                better="b"
              />
              <MetricRow
                label="True positives"
                a={compact(baseline.true_positives)}
                b={compact(engine.true_positives)}
                better="b"
              />
              <MetricRow
                label="False negatives (missed)"
                a={compact(baseline.false_negatives)}
                b={compact(engine.false_negatives)}
                better="b"
              />
              <MetricRow
                label="False positives"
                a={compact(baseline.false_positives)}
                b={compact(engine.false_positives)}
                better="b"
              />
              <MetricRow
                label="Analyst touches"
                a={compact(baseline.workload.analyst_touches)}
                b={compact(engine.workload.analyst_touches)}
                better="b"
                note="Scheduled reviews for the baseline; alerts raised for the engine"
              />
              <MetricRow
                label="Touches per material event caught"
                a={baseline.workload.touches_per_material_event_caught?.toFixed(2) ?? '—'}
                b={engine.workload.touches_per_material_event_caught?.toFixed(2) ?? '—'}
                better="b"
                note="Analyst effort per unit of genuine risk found"
              />
              <MetricRow
                label="Reviews that found nothing"
                a={percent(baseline.workload.wasted_review_rate)}
                b="n/a"
                note="A periodic review runs whether or not anything happened"
              />
              <MetricRow
                label="In-process compute latency (median)"
                a="n/a"
                b={`${((engine.measured_pipeline_latency?.median_microseconds ?? 0) / 1000).toFixed(
                  2,
                )} ms`}
                note="Resolution + classification + gate + scoring, excluding I/O"
              />
            </tbody>
          </Table>
        </div>
      </Card>

      {/* ------------------------------------------------- error attribution */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Why the engine missed events"
            subtitle="Every false negative attributed to a cause, so the weakness is locatable."
          />
          <CauseList causes={engine.miss_causes} total={engine.false_negatives} />
        </Card>
        <Card>
          <CardHeader
            title="Why the engine raised false positives"
            subtitle="The gate's severity floor sits at LOW, so MEDIUM-severity routine signal clears it."
          />
          <CauseList
            causes={engine.false_positive_causes ?? {}}
            total={engine.false_positives}
          />
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Why the baseline missed events"
          subtitle="Both causes are structural to a fixed-schedule policy, not tuning choices."
        />
        <CauseList causes={baseline.miss_causes} total={baseline.false_negatives} />
      </Card>

      {/* ------------------------------------------------------- assumptions */}
      <Card>
        <CardHeader
          title="Method and stated assumptions"
          subtitle="Every modelling choice that affects the numbers above, written down so the result can be argued with."
        />
        <dl className="mt-4 flex flex-col gap-3">
          {Object.entries(run.config.assumptions ?? {}).map(([key, value]) => (
            <div key={key}>
              <dt className="text-[12px] font-semibold text-primary">{humanise(key)}</dt>
              <dd className="mt-0.5 text-[12.5px] leading-relaxed text-secondary">{value}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-4">
          <Callout tone="info" title="Where the baseline's high false-positive rate comes from">
            A periodic review is indiscriminate rather than inaccurate: it sweeps
            whatever is in the file, so it picks up noise and signal alike. Its
            distinguishing weakness is latency and wasted effort, not recall. Both
            are reported above rather than collapsed into one number.
          </Callout>
        </div>
      </Card>
    </div>
  )
}

/* --------------------------------------------------------------- fragments */

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] text-muted">{label}</p>
      <p className="tnum text-[13px] font-medium text-primary">{value}</p>
    </div>
  )
}

function MetricRow({
  label,
  a,
  b,
  better,
  note,
}: {
  label: string
  a: string
  b: string
  better?: 'a' | 'b'
  note?: string
}) {
  return (
    <tr>
      <Td className="font-medium text-primary">{label}</Td>
      <Td
        align="right"
        className={cx('tnum', better === 'a' && 'font-semibold text-primary')}
      >
        {a}
      </Td>
      <Td
        align="right"
        className={cx('tnum', better === 'b' && 'font-semibold text-primary')}
      >
        {b}
      </Td>
      <Td className="text-[12px] text-muted">{note}</Td>
    </tr>
  )
}

function CauseList({
  causes,
  total,
}: {
  causes: Record<string, number>
  total: number
}) {
  const entries = Object.entries(causes).sort((left, right) => right[1] - left[1])

  if (!entries.length) {
    return <p className="mt-3 text-[13px] text-muted">No errors of this kind.</p>
  }

  const max = Math.max(...entries.map(([, count]) => count))

  return (
    <ul className="mt-4 flex flex-col gap-2.5">
      {entries.map(([cause, count]) => (
        <li key={cause}>
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-[12.5px] text-primary">{humanise(cause)}</p>
            <p className="tnum shrink-0 text-[12px] text-secondary">
              {count.toLocaleString('en-GB')}
              <span className="ml-1.5 text-muted">
                {total ? `${((count / total) * 100).toFixed(0)}%` : ''}
              </span>
            </p>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-l-[1px] bg-sunken">
            <div
              className="h-full rounded-r-[4px]"
              style={{
                width: `${(count / max) * 100}%`,
                background: 'var(--series-2)',
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}
