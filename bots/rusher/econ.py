"""Economy planner: greedy best action per resource, every candidate tried on a copy.

Each step lists the candidate actions (research, free picks, growth, founding, roads), runs
every one on a throwaway copy of the game (Turn.trials, one round trip for all of them) and
values the copy with `value()` below. The action with the best gain per resource spent runs
on the real game; then the next step starts from the new state. Stops when nothing gains.

The value is a hand-written estimate of what a state is worth to the economy: weighted
income over a horizon, population, cities, techs (with a bonus for effects income doesn't
show) and progress through the ages. All of it is computed here in Python; the engine only
answers what the income, population etc. of a copy are.
"""
from __future__ import annotations

import heapq

from hcbots.hexgrid import hex_distance, key, neighbors, within

W = {"sci": 1.0, "food": 1.05, "coins": 0.8}      # value per unit of income per round
PRICE = {"sci": 1.0, "food": 1.4, "coins": 1.0}   # price of a resource spent now
SCI_RESERVE = 3      # science spent on anything but research counts this much more
GAMMA = 0.86
END_ROUND = 11
W_POP, W_CITY, W_TECH, W_AGE = 1.2, 3.0, 0.8, 2.2
AGE_VALUE = [0, 1, 2.3, 3.8, 5.6]
GROW_STEP = 1.5      # value per city and round of one more growth step (keramik, dampfmaschine)
THREAT_W = 300       # weight of an enemy's economy victory coming close
# Effects income doesn't show (military for the rush, growth and trade helpers)
MIL_BONUS = {"sklaverei": 40, "belagerung": 60, "eisenverarbeitung": 6, "demokratie": 6,
             "taktik": 2, "nationalismus": 14, "gewehre": 6, "rittertum": 5,
             "militaergericht": 7, "dynamit": 25, "stadtmauern": 10, "stahl": 6,
             "maschinengewehr": 10, "theologie": 3, "philosophie": 4, "navigation": 5}


def horizon(rnd: int) -> float:
    r = max(1, min(8, END_ROUND - rnd + 1))
    return sum(GAMMA ** i for i in range(r))


class EconPlanner:
    def __init__(self, t, pool: list, slavery_pays: bool, threat_w: float = THREAT_W,
                 grow_step: float = GROW_STEP, coin_price: float = PRICE["coins"], def_w: float = 0.0,
                 alch_bonus: float = 0.0, chunk: bool = True, hold=(), mil_scale: float = 1.0, mil_from: int = 1):
        self.alch_bonus = alch_bonus
        self.chunk = chunk
        self.hold = set(hold)      # techs left to the strike (researched in the turn they hit)
        # military bonus only from round mil_from on (scaled): the rush techs crowd out the economy
        self.mil_scale = mil_scale if t.round >= mil_from else 0.0
        self.t = t
        self.def_w = def_w
        cap = t.capital()
        self.cap_id = cap["id"] if cap else None
        self.threat_w, self.grow_step = threat_w, grow_step
        self.price = dict(PRICE, coins=coin_price)
        self.pool = {x["k"]: x for x in pool}
        self.H = horizon(t.round)
        self.slavery_pays = slavery_pays
        # enemies' population at the end of the round (with what they can still grow) and
        # their economy-victory threshold
        self.threats = []
        for e in t.enemies():
            vo = t.q("victoryOption", "$S", {"$player": e})
            pop = sum(c["pop"] for c in t.cities(e))
            grow = 2 + len(t.cities(e)) if self.moves_after(e) else 0
            if vo and vo.get("frac"):
                self.threats.append((pop + grow, grow, vo["frac"]))

    def moves_after(self, e: int) -> bool:
        """Does enemy e still move this round after me?"""
        t = self.t
        n, first = len(t.S["players"]), t.S.get("startIdx", 0) or 0
        order = [(first + i) % n for i in range(n)]
        return order.index(e) > order.index(t.pi)

    # ------------------------------------------------------------------ value
    def calls(self):
        pi = self.t.pi
        return [("baseIncome", "$S", pi), ("popOf", "$S", pi), ("worldPop", "$S"),
                ("victoryOption", "$S", {"$player": pi}), ("$player", pi),
                ("defenseValue", "$S", {"$city": self.cap_id}) if self.cap_id is not None else ("popOf", "$S", pi)]

    def tech_bonus(self, k: str, ncities: int, inc: dict) -> float:
        H = self.H
        if k == "singularitaet":
            return 5000
        v = MIL_BONUS.get(k, 0) * self.mil_scale
        # one more paid growth step per city and round: growth is the bottleneck
        if k == "keramik":
            v += self.grow_step * H * min(ncities, 8)
        elif k == "rad":
            v += 1.2 * H * max(0, ncities - 1) + 2
        elif k == "dampfmaschine":
            v += self.grow_step * H * min(ncities, 8) + 0.35 * H * ncities
        elif k == "kartografie":
            v += 1.5 * max(0, 7 - ncities) + 1
        elif k == "alchemie":
            v += 0.12 * H * max(0, inc["sci"]) + 2 + self.alch_bonus
        elif k == "gilden":
            v += 0.12 * H * max(0, inc["coins"]) + 2
        elif k == "wiss_methode":
            v += 18 + 4 * min(4, H)
        t = self.pool.get(k)
        if t and t["age"] >= 3 and self.slavery_pays:
            v -= 15                         # the modern age ends slavery
        return v

    def value(self, res) -> tuple[float, dict]:
        inc, pop, world, vo, p, dcap = res
        techs = [k for k, v in (p or {}).get("techs", {}).items() if v]
        ncities = self._ncities
        v = self.H * sum(W[r] * inc[r] for r in W)
        v += W_POP * pop + W_CITY * ncities + W_TECH * len(techs)
        v += sum(self.tech_bonus(k, ncities, inc) for k in techs)
        if self.def_w and self.cap_id is not None:
            v += self.def_w * (dcap or 0)
        best = [-1, -1, -1, -1]
        for k in techs:
            x = self.pool.get(k)
            if x and 0 <= x["f"] < 4:
                best[x["f"]] = max(best[x["f"]], x["age"])
        late = min(1.8, 0.6 + self.t.round / 10)
        v += W_AGE * late * sum(AGE_VALUE[a + 1] for a in best) + (25 if min(best) >= 3 else 0)
        if world and vo and vo.get("frac"):
            q = min(1.2, pop / world / vo["frac"])
            v += 40 * q ** 6
        for epop, grow, frac in self.threats:
            q = min(1.3, epop / max(1, world + grow) / frac)
            v -= self.threat_w * q ** 10
        return v, p["res"]

    # ------------------------------------------------------------------ candidates
    def candidates(self):
        t = self.t
        out = []
        sci = t.q("available", "$S", t.pi, "sci")
        open_ = t.q("researchable", "$S", t.pi) or []
        costs = t.call([("techCost", "$S", t.pi, x) for x in open_])
        for x, c in zip(open_, costs):
            if c is not None and c <= sci and x["k"] not in self.hold:
                out.append(("tech:" + x["k"], [("research", {"tech": x["k"]})]))
        for kind, fn in (("freeTech", "freeTechOptions"), ("backPick", "backPickOptions"),
                         ("freePick", "freePickOptions")):
            for x in t.q(fn, "$S", t.pi) or []:
                if x["k"] in self.hold:
                    continue
                out.append(("pick:" + x["k"], [(kind, {"tech": x["k"]})]))
        for city in t.cities():
            cid = city["id"]
            out.append(("gfree:%d" % cid, [("growCity", {"cityId": cid, "mode": "free"})]))
            out.append(("grow:%d" % cid, [("growCity", {"cityId": cid, "mode": "paid"})]))
        for r, c in self.sites()[:6]:
            out.append(("found:%d,%d" % (r, c), [("foundCity", {"r": r, "c": c})]))
        if t.has("rad"):
            for tag, tiles in self.road_plans()[:4]:
                out.append((tag, [("buildRoad", {"r": r, "c": c, "target": 1}) for r, c in tiles]))
        return out

    def sites(self):
        t = self.t
        food = t.q("available", "$S", t.pi, "food")
        mine = t.cities()
        if food <= 0 or not mine:
            return []
        cand = set()
        for c in mine:
            for (r, cc) in within(c["r"], c["c"], 6):
                if t.grid.is_land(r, cc):
                    cand.add((r, cc))
        cand = [x for x in cand if all(hex_distance(x[0], x[1], o["r"], o["c"]) >= 3 for o in t.S["cities"])
                and not t.army_at(*x)]
        if not cand:
            return []
        calls = []
        for r, c in cand:
            calls += [("foundSiteError", "$S", t.pi, r, c), ("foundCost", "$S", t.pi, r, c),
                      ("settleGain", "$S", t.pi, r, c)]
        res = t.call(calls)
        scored = []
        for i, (r, c) in enumerate(cand):
            err, cost, g = res[3 * i: 3 * i + 3]
            if err or cost is None or cost > food or not g:
                continue
            v = self.H * (W["sci"] * g["sci"] + W["food"] * max(g["food"], -1) + W["coins"] * g["coins"])
            scored.append((v - 1.2 * cost, r, c))
        scored.sort(reverse=True)
        return [(r, c) for _, r, c in scored]

    def road_plans(self):
        """For every own city off the capital's road net: the cheapest tiles to connect it
        (Dijkstra over own or neutral land, a tile with road or an own city costs nothing)."""
        t = self.t
        cap = t.capital()
        if not cap:
            return []
        roads = t.S.get("roads") or {}
        foreign = set()
        for i in range(len(t.S["players"])):
            if i != t.pi:
                foreign.update(t.q("controlledTiles", "$S", i) or [])
        own_city = {(c["r"], c["c"]) for c in t.cities()}
        other_city = {(c["r"], c["c"]) for c in t.S["cities"]} - own_city

        def free(rc):
            return rc in own_city or roads.get(key(*rc), 0) >= 1

        def ok(rc):
            return (t.grid.is_tile(*rc) and t.grid.terrain(*rc) not in ("M",)
                    and rc not in other_city and (key(*rc) not in foreign or rc in own_city))
        # the capital's net
        net, stack = {(cap["r"], cap["c"])}, [(cap["r"], cap["c"])]
        while stack:
            rc = stack.pop()
            for n in neighbors(*rc):
                if n not in net and ok(n) and free(n):
                    net.add(n)
                    stack.append(n)
        plans = []
        for city in t.cities():
            goal = (city["r"], city["c"])
            if city.get("cap") or goal in net:
                continue
            dist = {s: 0 for s in net}
            prev = {}
            q = [(0, s) for s in net]
            heapq.heapify(q)
            found = None
            while q:
                d, rc = heapq.heappop(q)
                if d > dist.get(rc, 99) or d > 8:
                    continue
                if rc == goal:
                    found = d
                    break
                for n in neighbors(*rc):
                    if not ok(n) or not t.grid.is_land(*n) and n not in own_city:
                        continue
                    nd = d + (0 if free(n) else 1)
                    if nd < dist.get(n, 99):
                        dist[n] = nd
                        prev[n] = rc
                        heapq.heappush(q, (nd, n))
            if not found:
                continue
            tiles, rc = [], goal
            while rc in prev:
                rc = prev[rc]
                if not free(rc):
                    tiles.append(rc)
            if tiles:
                plans.append((found, "road:%d" % city["id"], tiles))
        plans.sort()
        return [(tag, tiles) for _, tag, tiles in plans]

    # ------------------------------------------------------------------ plan
    def run(self, max_steps: int = 40, keep_coins: int = 0) -> list:
        t = self.t
        done = []
        failed = set()
        for _ in range(max_steps):
            if t.over:
                break
            self._ncities = len(t.cities())
            cands = [c for c in self.candidates() if c[0] not in failed]
            if not cands:
                break
            calls = self.calls()
            trials = [([], calls)] + [(acts, calls) for _, acts in cands]
            res = t.trials(trials)
            if res[0] is None:
                break
            base, before = self.value(res[0])
            best = None
            top = None
            for (tag, acts), r in zip(cands, res[1:]):
                if r is None:
                    continue
                self._ncities = len(t.cities()) + (1 if tag.startswith("found:") else 0)
                v, after = self.value(r)
                self._ncities = len(t.cities())
                dv = v - base
                if dv <= 0.05:
                    continue
                if after["coins"] < keep_coins and after["coins"] < before["coins"]:
                    continue
                research = tag.startswith(("tech:", "pick:"))
                spent = sum(self.price[k] * (before[k] - after[k]) * (1 if k != "sci" or research else SCI_RESERVE)
                            for k in self.price)
                ratio = dv / (max(0, spent) + 2)
                if best is None or ratio > best[0]:
                    best = (ratio, dv, tag, acts)
                if top is None or dv > top[1]:
                    top = (ratio, dv, tag, acts)
            if best is None:
                break
            # big chunks: an action worth much more in total wins if its ratio is not much
            # worse (else small things eat the food a new city needs)
            if self.chunk and top is not best and top[1] > 2.5 * best[1] and top[0] > 0.45 * best[0]:
                best = top
            _, dv, tag, acts = best
            err = None
            for a, p in acts:
                err = t.act(a, **p)
                if err:
                    break
            if err:
                failed.add(tag)
            else:
                done.append((tag, round(dv, 1)))
        return done
