import { el, svg } from "./dom.js";

/** @typedef {{name: string, values: number[]}} Series */
/** @typedef {{kind: "chart", title: string, chart_type: "line"|"bar"|"area"|"pie"|"scatter", x: (string|number)[], series: Series[], x_label: string|null, y_label: string|null, unit: string|null}} Chart */

const WIDTH = 480;
const HEIGHT = 230;
const LEFT = 60;
const RIGHT = WIDTH - 22;
const TOP = 18;
const BOTTOM = HEIGHT - 30;
const FORMAT = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 4, notation: "compact" });

/** @param {number} index */
function color(index) {
  return `chat-mark-${index % 6}`;
}

/** @param {string | number} value */
function label(value) {
  return typeof value === "number" ? FORMAT.format(value) : value;
}

/** @param {string} text */
function compact(text) {
  return text.length > 14 ? `${text.slice(0, 13)}…` : text;
}

/** @param {Chart} card */
function pie(card) {
  const values = card.series[0].values;
  const total = values.reduce((sum, value) => sum + value, 0);
  const circumference = 2 * Math.PI * 51;
  let offset = 0;
  return values.map((value, index) => {
    const length = (value / total) * circumference;
    const segment = svg(
      "circle",
      { class: color(index), cx: WIDTH / 2, cy: HEIGHT / 2, r: 51, fill: "none", "stroke-width": 101, "stroke-dasharray": `${length} ${circumference}`, "stroke-dashoffset": -offset, transform: `rotate(-90 ${WIDTH / 2} ${HEIGHT / 2})` },
      svg("title", null, `${card.x[index]}: ${value}`),
    );
    offset += length;
    return value > 0 ? segment : null;
  });
}

/** @param {Chart} card */
function cartesian(card) {
  const filled = card.chart_type === "bar" || card.chart_type === "area";
  const values = card.series.flatMap((series) => series.values);
  const min = Math.min(...values, ...(filled ? [0] : []));
  const max = Math.max(...values, ...(filled ? [0] : []));
  const low = min === max ? min - 1 : min;
  const high = min === max ? max + 1 : max;
  /** @param {number} value */
  const y = (value) => BOTTOM - (BOTTOM - TOP) * ((value - low) / (high - low));
  const numeric = card.x.filter((value) => typeof value === "number");
  const xMin = Math.min(...numeric);
  const xMax = Math.max(...numeric);
  const step = (RIGHT - LEFT) / card.x.length;
  /** @param {number} index */
  const x = (index) => {
    if (card.chart_type === "scatter" && xMin !== xMax) {
      return LEFT + (RIGHT - LEFT) * ((numeric[index] - xMin) / (xMax - xMin));
    }
    return card.chart_type === "scatter" ? (LEFT + RIGHT) / 2 : LEFT + step * (index + 0.5);
  };
  const ticks = Array.from({ length: 5 }, (_, index) => {
    const value = low + ((high - low) * index) / 4;
    return svg("g", null,
      svg("line", { class: "chat-chart-grid", x1: LEFT, x2: RIGHT, y1: y(value), y2: y(value) }),
      svg("text", { x: LEFT - 8, y: y(value) + 4, "text-anchor": "end" }, label(value)),
    );
  });
  const sortedX = card.chart_type === "scatter" ? [...new Set(numeric)].sort((a, b) => a - b) : card.x;
  const xTicks = sortedX.flatMap((value, index) => {
    const every = Math.ceil(sortedX.length / 4);
    const position = card.chart_type === "scatter" ? x(numeric.indexOf(Number(value))) : x(index);
    return index % every === 0
      ? [svg("text", { x: position, y: HEIGHT - 8, "text-anchor": "middle" }, compact(label(value)))]
      : [];
  });
  const series = card.series.map(({ name, values: points }, index) => {
    const group = svg("g", { class: color(index) });
    if (card.chart_type === "line" || card.chart_type === "area") {
      const positions = points.map((value, i) => `${x(i)},${y(value)}`);
      if (card.chart_type === "area") {
        group.append(svg("polygon", { class: "chat-chart-area", points: [`${x(0)},${y(0)}`, ...positions, `${x(points.length - 1)},${y(0)}`].join(" ") }));
      }
      group.append(svg("polyline", { fill: "none", "stroke-width": 2, points: positions.join(" ") }));
    }
    const barWidth = (step * 0.72) / card.series.length;
    points.forEach((value, i) => {
      const mark = card.chart_type === "bar"
        ? svg("rect", { x: x(i) - step * 0.36 + barWidth * index, y: Math.min(y(0), y(value)), width: barWidth, height: Math.max(1, Math.abs(y(0) - y(value))) })
        : svg("circle", { cx: x(i), cy: y(value), r: 3 });
      mark.append(svg("title", null, `${name} · ${card.x[i]}: ${value}${card.unit === null ? "" : ` ${card.unit}`}`));
      group.append(mark);
    });
    return group;
  });
  return [...ticks, ...xTicks, ...series];
}

/** @param {Chart} card @param {(columns: string[], rows: (string | number)[][]) => HTMLElement} table */
export function cardChart(card, table) {
  const isPie = card.chart_type === "pie";
  const names = isPie ? card.x.map(String) : card.series.map((series) => series.name);
  const legend = el("ul", { class: "chat-chart-legend" }, names.map((name, index) =>
    el("li", null, el("span", { class: color(index), "aria-hidden": "true" }), name)));
  const axes = [card.x_label, card.y_label, card.unit].filter((text) => text !== null).join(" · ");
  return el("section", { class: "chat-card chat-chart" },
    el("h3", null, card.title),
    axes === "" ? null : el("p", { class: "muted" }, axes),
    svg("svg", { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, role: "img", "aria-label": `${card.title}, ${card.chart_type} chart` }, isPie ? pie(card) : cartesian(card)),
    legend,
    el("details", null, el("summary", null, "Chart data"), table(
      [card.x_label ?? "X", ...card.series.map((series) => series.name)],
      card.x.map((value, index) => [value, ...card.series.map((series) => series.values[index])]),
    )),
  );
}
