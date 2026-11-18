"""Rusher: slavery economy and an early two-turn siege of the nearest enemy capital.

Every decision is made here in Python; the engine only answers rule questions (reach, costs,
attack and defense values) and applies the actions.

Turn plan:
  1. free picks, research (military priority list; leftover science on cheap techs, never a
     modern-age tech while slavery pays)
  2. found cities on the best sites food allows
  3. with slavery: grow every city, then sacrifice one population in every city (+7 coins)
  4. military: strike the target capital if a winning siege is affordable this turn, else stage
     armies just outside its range; guard the own capital when an enemy could take it
  5. armies never stay in cities; all remaining coins go into power (resources don't carry
     over to the next turn, power does)
"""
from __future__ import annotations

import math

from hcbots.hexgrid import hex_distance, neighbors, within

from .econ import EconPlanner

# Techs bought first, in this order, whenever available and affordable.
PRIORITY = ["sklaverei", "eisenverarbeitung", "belagerung", "demokratie", "taktik",
            "nationalismus", "gewehre", "rittertum", "militaergericht", "dynamit", "stahl",
            "stadtmauern"]
# Cheap enablers: the first tech of a field and age rolls the next age of that field.
ENABLERS = {2: ["stahl", "militaerlogistik", "burgenbau", "schiesspulver"],
            3: ["rittertum", "kundschafterei", "theologie", "kartografie", "buerokratie"]}
# Tech value = HORIZON x weighted income gain + bonus (for effects income doesn't show).
INCOME_W = {"sci": 1.0, "food": 1.0, "coins": 1.3}
HORIZON = 4
TECH_BONUS = {"sklaverei": 40, "belagerung": 60, "eisenverarbeitung": 6, "demokratie": 6,
              "taktik": 2, "nationalismus": 14, "gewehre": 6, "rittertum": 5,
              "militaergericht": 7, "dynamit": 25, "stadtmauern": 10, "stahl": 6,
              "keramik": 8, "rad": 4, "theologie": 8, "kartografie": 4, "maschinengewehr": 10,
              "dampfmaschine": 8, "gilden": 4, "alchemie": 3}
# Techs that may take coins (pay() converts them) up to the given round: the rush needs them now.
COIN_TECHS = {"sklaverei": 2, "belagerung": 3}
FROM_ROUND = 2          # first round in which armies march
STAGE_ARMIES = 3        # armies kept for the strike
SITE_WEIGHTS = (1.0, 1.2, 1.3)   # sci, food, coins of a new city's first income


class Rusher:
    name = "rusher"

    def __init__(self, stage_armies: int = STAGE_ARMIES, from_round: int = FROM_ROUND,
                 sacrifice_capital: bool = True, soft_share: float = 0.0, defend: bool = True,
                 garrison_max: int = 0, garrison_k: float = 1.0, garrison_share: float = 1.0,
                 **econ):
        self.garrison_max, self.garrison_k, self.garrison_share = garrison_max, garrison_k, garrison_share
        self.soft_share = soft_share
        self.use_defend = defend
        self.econ = econ          # EconPlanner options: threat_w, grow_step, coin_price
        self.stage_armies = stage_armies
        self.from_round = from_round
        self.sacrifice_capital = sacrifice_capital

    # ------------------------------------------------------------------ placement
    def place(self, seat, plan):
        best = None
        for o, row in enumerate(seat["choices"]):
            for cell, y in enumerate(row):
                if not y:
                    continue
                v = y["sci"] * 1.0 + y["food"] * 1.2 + y["coins"] * 1.3
                if best is None or v > best[0]:
                    best = (v, o, cell)
        return {"o": best[1], "cell": best[2]} if best else None

    # ------------------------------------------------------------------ the turn
    def turn(self, t):
        self.t = t
        self.guard_ids = set()
        tgt = self.target()
        # a siege that takes the target capital this turn wins the game: first
        if tgt and (t.S.get("sieges") or {}).get(f"{t.pi}|{tgt['id']}", 0) >= 1 and self.strike(tgt):
            return
        if self.use_defend:
            self.defend()
        if self.garrison_max:
            self.garrison()
        if t.round >= self.from_round and tgt:
            self.strike(tgt)
        if t.over:
            return
        pool = t.q("techPool", "$S") or []
        self.plan_log = EconPlanner(t, pool, self.slavery_pays(), **self.econ).run()
        if t.over:
            return
        self.sacrifice_all()
        self.military()
        if t.over:
            return
        self.leave_cities()
        self.spend_rest()

    def sacrifice_all(self):
        t = self.t
        if not t.q("slaveryUsable", {"$player": t.pi}):
            return
        for city in sorted(t.cities(), key=lambda c: -c["pop"]):
            if city["pop"] < 2 or not self.econ_safe(-1):
                continue
            if city.get("cap"):
                rng = t.q("attackRange", "$S", t.pi)
                pow_ = t.q("powerOf", "$S", t.pi)
                threat = self.enemy_attack(city, len(self.ring(city, rng)))
                if threat and t.q("defenseValue", "$S", {"$city": city["id"]}, pow_) - 2 <= threat:
                    continue
            t.act("sacrifice", cityId=city["id"])

    # ------------------------------------------------------------------ research
    def slavery_pays(self) -> bool:
        return self.t.has("sklaverei") or "sklaverei" in self.t.me["avail"]

    def tech_values(self, techs) -> dict:
        """Value of each tech: the income it adds (tried on a fork of the game, so every rule
        is the engine's) over a few rounds, plus a fixed bonus for what income doesn't show."""
        t = self.t
        base = t.q("income", "$S", t.pi)
        owned_ages = {(x["f"], x["age"]) for x in (t.q("techPool", "$S") or []) if t.has(x["k"])}
        out = {}
        for x in techs:
            k = x["k"]
            if k == "singularitaet":
                out[k] = 1000
                continue
            f = t.fork()
            try:
                f.q("grantTech", "$S", t.pi, k, "test")
                inc = f.q("income", "$S", t.pi)
            except RuntimeError:
                inc = base
            finally:
                f.close()
            gain = sum(INCOME_W[r] * (inc[r] - base[r]) for r in INCOME_W)
            v = HORIZON * gain + TECH_BONUS.get(k, 0)
            if x["age"] < 3 and (x["f"], x["age"]) not in owned_ages:
                v += 3                      # the first tech of an age rolls the next one
            if x["age"] >= 3 and self.slavery_pays():
                v -= 15                     # modern age: slavery is gone
            out[k] = v
        return out

    def research(self, leftover: bool = False, sci_only: bool = False):
        """Best value per science first, paid from science only (doResearch would take coins
        for the rest) – except slavery, which pays for itself in the same turn."""
        t = self.t
        for _ in range(15):
            open_ = t.q("researchable", "$S", t.pi) or []
            if not open_:
                return
            costs = t.call([("techCost", "$S", t.pi, x) for x in open_])
            sci = t.me["res"]["sci"]
            avail_sci = t.q("available", "$S", t.pi, "sci")
            ok = []
            for x, c in zip(open_, costs):
                if c is None:
                    continue
                budget = (avail_sci if x["k"] in COIN_TECHS and not sci_only and t.round <= COIN_TECHS[x["k"]]
                          else sci)
                if c <= budget:
                    ok.append((x, c))
            if not ok:
                return
            vals = self.tech_values([x for x, _ in ok])
            score, pick = max((vals[x["k"]] / max(c, 1), x["k"]) for x, c in ok)
            if score <= 0 or t.act("research", tech=pick):
                return

    def picks(self):
        t = self.t
        for kind, fn in (("freeTech", "freeTechOptions"), ("backPick", "backPickOptions"),
                         ("freePick", "freePickOptions")):
            for _ in range(3):
                opts = t.call([(fn, "$S", t.pi)])[0] or []
                if not opts:
                    break
                vals = self.tech_values(opts)
                pick = max(opts, key=lambda o: (vals[o["k"]], o.get("c", 0)))["k"]
                if t.act(kind, tech=pick):
                    break

    # ------------------------------------------------------------------ founding
    def found(self):
        t = self.t
        for _ in range(4):
            food = t.q("available", "$S", t.pi, "food")
            if food <= 0:
                return
            mine = t.cities()
            if not mine:
                return
            cand = set()
            for c in mine:
                for (r, cc) in within(c["r"], c["c"], 6):
                    if t.grid.is_land(r, cc):
                        cand.add((r, cc))
            cand = [x for x in cand if all(hex_distance(x[0], x[1], o["r"], o["c"]) >= 3 for o in t.S["cities"])]
            if not cand:
                return
            calls = []
            for r, c in cand:
                calls += [("foundSiteError", "$S", t.pi, r, c), ("foundCost", "$S", t.pi, r, c),
                          ("settleGain", "$S", t.pi, r, c)]
            res = t.call(calls)
            best = None
            for i, (r, c) in enumerate(cand):
                err, cost, gain = res[3 * i: 3 * i + 3]
                if err or cost is None or cost > food or not gain:
                    continue
                v = gain["sci"] * SITE_WEIGHTS[0] + gain["food"] * SITE_WEIGHTS[1] + gain["coins"] * SITE_WEIGHTS[2]
                v -= 0.25 * cost
                # with slavery, any city is worth +6 coins a round
                if self.slavery_pays():
                    v += 3
                if v > 0 and (best is None or v > best[0]):
                    best = (v, r, c)
            if not best or t.act("foundCity", r=best[1], c=best[2]):
                return

    # ------------------------------------------------------------------ growth
    def grow_and_sacrifice(self):
        t = self.t
        slavery = t.q("slaveryUsable", {"$player": t.pi})
        for city in sorted(t.cities(), key=lambda c: c["pop"]):
            if t.q("freeGrowthAvailable", "$S", t.pi, {"$city": city["id"]}):
                t.act("growCity", cityId=city["id"], mode="free")
            for _ in range(3):
                if t.act("growCity", cityId=city["id"], mode="paid"):
                    break
        if not slavery:
            return
        for city in sorted(t.cities(), key=lambda c: -c["pop"]):
            if city.get("cap") and not self.sacrifice_capital and city["pop"] <= 3:
                continue
            if city["pop"] < 2 or not self.econ_safe(-1):
                continue
            t.act("sacrifice", cityId=city["id"])

    def econ_safe(self, delta: int) -> bool:
        """Would every enemy stay below its economy-victory threshold if my population changed
        by `delta` and each enemy grew by what it can grow until the round ends?"""
        t = self.t
        pops = {i: sum(c["pop"] for c in t.cities(i)) for i in range(len(t.S["players"]))}
        world = sum(pops.values()) + delta
        if not hasattr(self, "_vo") or self._vo[0] != t.round:
            self._vo = (t.round, {e: t.q("victoryOption", "$S", {"$player": e}) for e in t.enemies()})
        for e in t.enemies():
            o = self._vo[1][e]
            grow = 2 + len(t.cities(e))
            mine, w = pops[e] + grow, world + grow
            if mine * o["den"] > w * o["num"] if o.get("strict", True) else mine * o["den"] >= w * o["num"]:
                return False
        return True

    # ------------------------------------------------------------------ military helpers
    def target(self):
        """The enemy capital nearest to the own capital, by land path if there is one."""
        t = self.t
        home = t.capital()
        if not home:
            return None
        caps = [c for c in t.S["cities"] if c["owner"] != t.pi and c.get("cap")
                and t.S["players"][c["owner"]].get("kind") != "barbar"
                and not t.S["players"][c["owner"]].get("dead")]
        if not caps:
            return None
        field = t.grid.distance_field([(home["r"], home["c"])])

        def d(c):
            near = [field[n] for n in neighbors(c["r"], c["c"]) if n in field]
            return (min(near) + 1) if near else 100 + hex_distance(home["r"], home["c"], c["r"], c["c"])
        return min(caps, key=d)

    def reach(self, army) -> dict:
        res = self.t.q("armyReach", "$S", {"$army": army["id"]}) or []
        out = {}
        for k, cost in res:
            r, c = k.split(",")
            out[(int(r), int(c))] = cost
        return out

    def spots(self, city, rng):
        t = self.t
        out = []
        for r, c in within(city["r"], city["c"], rng):
            if not t.grid.is_land(r, c) or t.city_at(r, c):
                continue
            a = t.army_at(r, c)
            if a and a["owner"] != t.pi:
                continue
            out.append((r, c))
        return out

    @staticmethod
    def match(options: dict, spots: list) -> dict:
        """Max bipartite matching armies -> spots (Kuhn). options: aid -> set of spots."""
        owner = {}

        def try_(aid, seen):
            for s in options[aid]:
                if s in seen:
                    continue
                seen.add(s)
                if s not in owner or try_(owner[s], seen):
                    owner[s] = aid
                    return True
            return False
        for aid in sorted(options, key=lambda a: len(options[a])):
            try_(aid, set())
        return {aid: s for s, aid in owner.items()}

    def attack_per_army(self, power):
        t = self.t
        per = power + (3 if t.has("belagerung") else 0)
        return per * (2 if t.has("dynamit") else 1)

    # ------------------------------------------------------------------ military
    def military(self):
        t = self.t
        if t.round < self.from_round:
            return
        tgt = self.target()
        if not tgt:
            return
        if not self.strike(tgt):
            self.stage(tgt)

    def strike(self, tgt) -> bool:
        """Move armies into range of the target and buy the power for a winning siege this
        turn, if coins allow. Each variant (0, 1, 2 ... new armies) is tried on a fork of the
        game first; the one with the most armies in range wins. Returns True if one ran."""
        t = self.t
        rng = t.q("attackRange", "$S", t.pi)
        spots = self.spots(tgt, rng)
        build = [c for c in t.cities() if not t.army_at(c["r"], c["c"])
                 and any(hex_distance(c["r"], c["c"], s[0], s[1]) <= 4 for s in spots)]
        build.sort(key=lambda c: hex_distance(c["r"], c["c"], tgt["r"], tgt["c"]))
        best = None
        for extra in range(0, min(len(build), len(spots)) + 1):
            f = t.fork()
            try:
                r = self.run_strike(f, tgt, extra, build)
            finally:
                f.close()
            if r and (best is None or r > best[0]):
                best = (r, extra)
        if best is None:
            return False
        return self.run_strike(t, tgt, best[1], build) is not None

    def run_strike(self, t, tgt, extra, build):
        saved, self.t = self.t, t          # the helpers ask self.t – here the fork
        try:
            return self._run_strike(t, tgt, extra, build)
        finally:
            self.t = saved

    def _run_strike(self, t, tgt, extra, build):
        """Place the armies, build `extra` new ones and buy the power for attack > defense.
        Returns a score (armies in range, coins left) or None if the coins don't reach."""
        rng = t.q("attackRange", "$S", t.pi)
        spot_list = self.spots(tgt, rng)
        spots = set(spot_list)
        placed, failed = set(), set()
        for _ in range(12):
            free = {s for s in spots if not t.army_at(*s)}
            options, reaches = {}, {}
            for a in t.armies():
                if a["id"] in placed or a["id"] in failed or a["id"] in self.guard_ids:
                    continue
                if (a["r"], a["c"]) in spots:
                    placed.add(a["id"])
                    continue
                if a["mp"] <= 0 or a.get("halted"):
                    continue
                reaches[a["id"]] = self.reach(a)
                opts = {s for s in reaches[a["id"]] if s in free}
                if opts:
                    options[a["id"]] = opts
            m = self.match(options, sorted(free))
            if not m:
                break
            # the longest move first: shorter ones could block its path
            aid, s = max(m.items(), key=lambda kv: reaches[kv[0]][kv[1]])
            if t.act("moveArmy", armyId=aid, r=s[0], c=s[1]):
                failed.add(aid)
            else:
                placed.add(aid)
        built = 0
        for c in build:
            if built >= extra:
                break
            if t.act("buildArmy", cityId=c["id"]):
                return None
            a = t.army_at(c["r"], c["c"])
            reach = self.reach(a)
            opts = [s for s in spots if s in reach and not t.army_at(*s)]
            if not opts:
                return None
            s = min(opts, key=lambda s: reach[s])
            if t.act("moveArmy", armyId=a["id"], r=s[0], c=s[1]):
                return None
            built += 1
        if built < extra:
            return None
        n = sum(1 for a in t.armies() if hex_distance(a["r"], a["c"], tgt["r"], tgt["c"]) <= rng
                and not t.city_at(a["r"], a["c"]))
        if not n:
            return None
        defense = t.q("defenseValue", "$S", {"$city": tgt["id"]})
        base = t.q("powerOf", "$S", t.pi)
        need = 0
        while n * self.attack_per_army(base + need) <= defense:
            need += 1
        price = t.q("powerPrice", "$S", t.pi)
        coins = t.q("available", "$S", t.pi, "coins")
        if need * price > coins:
            return None
        if need and t.act("buyPower", n=need):
            return None
        return (n, coins - need * price)

    def stage(self, tgt):
        """Out of reach: build up to stage_armies and march them to just outside the target's
        range, so that next turn they can all strike together."""
        t = self.t
        rng = t.q("attackRange", "$S", t.pi)
        field = t.grid.distance_field([(tgt["r"], tgt["c"])])
        own = [a for a in t.armies() if a["id"] not in self.guard_ids]
        cities = [c for c in t.cities() if not t.army_at(c["r"], c["c"])]
        cities.sort(key=lambda c: field.get((c["r"], c["c"]), 99))
        for c in cities:
            if len(own) >= self.stage_armies:
                break
            cost = t.q("armyCost", "$S", t.pi)
            if t.q("available", "$S", t.pi, "coins") < cost or t.act("buildArmy", cityId=c["id"]):
                break
            own.append(t.army_at(c["r"], c["c"]))
        for a in list(t.armies()):
            if a["mp"] <= 0 or a.get("halted") or a["id"] in self.guard_ids:
                continue
            reach = self.reach(a)
            here = (a["r"], a["c"])
            tiles = [s for s in reach if not t.army_at(*s)]
            if not t.city_at(*here):
                tiles.append(here)
            if not tiles:
                continue

            def score(s):
                d = hex_distance(s[0], s[1], tgt["r"], tgt["c"])
                fd = field.get(s, 99)
                # just outside the range is best; inside it without a winning strike is no use
                inside = 1 if d <= rng else 0
                return (inside, max(fd, rng + 1), fd)
            s = min(tiles, key=score)
            if s != here:
                t.act("moveArmy", armyId=a["id"], r=s[0], c=s[1])

    # ------------------------------------------------------------------ defense
    def enemy_attack(self, city, slots: int, new_armies: bool = True) -> int:
        """The strongest attack one enemy could bring against `city` at the end of its next
        turn: its armies that can get there plus new ones from its nearby cities, with its
        power after the decay plus its whole coin budget (income and slavery) - split between
        new armies and power in the way that hits hardest. At most `slots` attackers fit."""
        t = self.t
        worst = 0
        for e in t.enemies():
            ep = t.S["players"][e]
            erng, inc, price, div, p0, acost = t.call([
                ("attackRange", "$S", e), ("income", "$S", e), ("powerPrice", "$S", e),
                ("powerDecay", {"$player": e}), ("powerOf", "$S", e), ("armyCost", "$S", e)])
            erng = erng or 1
            move = 9 if ep["techs"].get("luftwaffe") else 6 if ep["techs"].get("panzerschiff") else 3
            if ep["techs"].get("eisenbahn") or (t.S.get("roads") and ep["techs"].get("rad")):
                move += 2
            near = move + erng
            k0 = sum(1 for a in t.armies(e) if hex_distance(a["r"], a["c"], city["r"], city["c"]) <= near)
            builders = sum(1 for c in t.cities(e) if hex_distance(c["r"], c["c"], city["r"], city["c"]) <= near)
            if not new_armies:
                builders = 0
            if not k0 and not builders:
                continue
            p0 = p0 or 0
            pw0 = p0 - math.ceil(p0 / (div or 2))
            budget = (inc or {}).get("coins", 0)
            if ep["techs"].get("sklaverei"):
                budget += 7 * sum(1 for c in t.cities(e) if c["pop"] >= 2)
            price = max(price or 5, 1)
            n_armies = len(t.armies(e))
            mult = max(1, (acost or 5) // max(1, n_armies + 1))
            spent = 0
            for j in range(0, builders + 1):
                if j:
                    spent += mult * (n_armies + j)
                if spent > budget:
                    break
                k = min(k0 + j, slots)
                if k <= 0:
                    continue
                per = pw0 + (budget - spent) // price
                per += 3 if ep["techs"].get("belagerung") else 0
                per *= 2 if ep["techs"].get("dynamit") else 1
                worst = max(worst, k * per)
        return worst

    def ring(self, city, rng):
        t = self.t
        return [(r, c) for r, c in within(city["r"], city["c"], rng)
                if t.grid.is_land(r, c) and not t.city_at(r, c)]

    def defend(self):
        """Hold every city under siege (one hit taken: the next one takes it) and the capital
        whenever an enemy could hit it: helpers next to the city and power until the defense
        beats the strongest attack the enemy could bring. Each plan (0, 1, 2 ... helpers) is
        tried on a fork; the cheapest that holds runs."""
        t = self.t
        coins = t.q("available", "$S", t.pi, "coins")
        sieges = t.S.get("sieges") or {}
        order = sorted(t.cities(), key=lambda c: (not c.get("cap"), -c["pop"]))
        for city in order:
            hit = any(sieges.get(f"{e}|{city['id']}", 0) >= 1 for e in t.enemies())
            if not hit and not city.get("cap"):
                continue
            if hit:
                plan = self.defense_plan(city, True)
                if plan and plan[0] > 0 and (city.get("cap") or (plan[2] and plan[0] <= 4 * city["pop"] + 6)):
                    self.run_defense(t, city, plan[1], commit=True, full=True)
            elif self.soft_share > 0:
                # not hit yet: hold against the armies already near, if that is cheap
                plan = self.defense_plan(city, False)
                if plan and 0 < plan[0] <= self.soft_share * coins and plan[2]:
                    self.run_defense(t, city, plan[1], commit=True, full=False)

    def potential(self, city) -> int:
        """My strongest answer at `city` next turn, the way the KI judges it before a first
        hit: power from my coin income times the armies in range and one move away (plus a
        new one), and the paid growth. The KI only hits where its next attack beats this."""
        t = self.t
        rng, mp, pnow, price, inc, grow, decay, cost1 = t.call([
            ("projectRange", "$S", t.pi), ("moveAllowance", "$S", t.pi), ("powerOf", "$S", t.pi),
            ("powerPrice", "$S", t.pi), ("income", "$S", t.pi), ("growLimits", "$S", t.pi),
            ("powerDecay", {"$player": t.pi}), ("armyCost", "$S", t.pi)])
        helpers = comers = 0
        for a in t.armies():
            d = hex_distance(a["r"], a["c"], city["r"], city["c"])
            if d <= rng:
                helpers += 1
            elif d <= mp + rng:
                comers += 1
        own = t.S["players"][t.pi].get("power", 0) or 0
        after = own - min(own, math.ceil((pnow or 0) / (decay or 2)))
        budget = max(0, (inc or {}).get("coins", 0))
        d0 = t.q("defenseValue", "$S", {"$city": city["id"]})
        per_pop = 2 if city.get("cap") else 1
        best = d0
        for m in (0, 1):
            c = cost1 if m else 0
            if c > budget:
                break
            pw = after + (budget - c) // max(price or 5, 1)
            best = max(best, d0 - (pnow or 0) * helpers + pw * (helpers + comers + m)
                       + (grow or {}).get("paid", 1) * per_pop)
        return best

    def garrison(self):
        """Keep armies next to the capital while an enemy's next attack would beat my answer
        to it: they multiply bought power in the defense, and the KI counts them."""
        t = self.t
        cap = t.capital()
        if not cap:
            return
        rng = t.q("attackRange", "$S", t.pi)
        ring = self.ring(cap, rng)
        for _ in range(self.garrison_max):
            mine = [a for a in t.armies() if hex_distance(a["r"], a["c"], cap["r"], cap["c"]) <= rng]
            for a in mine:
                self.guard_ids.add(a["id"])
            threat = self.enemy_attack(cap, len(ring) - len(mine))
            if not threat or self.potential(cap) * self.garrison_k > threat or len(mine) >= self.garrison_max:
                return
            cost = t.q("armyCost", "$S", t.pi)
            if cost > t.q("available", "$S", t.pi, "coins") * self.garrison_share:
                return
            if t.army_at(cap["r"], cap["c"]) or t.act("buildArmy", cityId=cap["id"]):
                return
            a = t.army_at(cap["r"], cap["c"])
            reach = self.reach(a)
            free = [s for s in ring if not t.army_at(*s) and s in reach]
            if not free:
                return
            s = min(free, key=lambda s: reach[s])
            t.act("moveArmy", armyId=a["id"], r=s[0], c=s[1])
            self.guard_ids.add(a["id"])

    def defense_plan(self, city, full: bool):
        """(cost, helpers, holds): the cheapest plan that holds, else the one with the best
        margin for all the coins there are."""
        best = None
        for h in range(0, 4):
            f = self.t.fork()
            try:
                r = self.run_defense(f, city, h, full=full)
            finally:
                f.close()
            if r is None:
                continue
            holds, cost, margin = r
            key = (holds, -cost if holds else margin)
            if best is None or key > best[0]:
                best = (key, cost, h, holds)
        return None if best is None else best[1:]

    def run_defense(self, t, city, helpers, commit=False, full=True):
        saved, self.t = self.t, t
        try:
            return self._run_defense(t, city, helpers, commit, full)
        finally:
            self.t = saved

    def _run_defense(self, t, city, helpers, commit, full):
        """Bring `helpers` more armies next to the city, then buy the power the defense needs.
        Returns the coins it costs, or None if they don't reach."""
        rng = t.q("attackRange", "$S", t.pi)
        ring = self.ring(city, rng)
        coins0 = t.q("available", "$S", t.pi, "coins")
        cid = city["id"]
        # population defends twice in the capital: grow it first
        for _ in range(3):
            if t.act("growCity", cityId=cid, mode="free") and t.act("growCity", cityId=cid, mode="paid"):
                break
        for _ in range(helpers):
            free = [s for s in ring if not t.army_at(*s)]
            if not free:
                return None
            # the nearest own army that can get there, else a new one from the city
            best = None
            for a in t.armies():
                if a["mp"] <= 0 or a.get("halted") or a["id"] in self.guard_ids:
                    continue
                if hex_distance(a["r"], a["c"], city["r"], city["c"]) <= rng and not t.city_at(a["r"], a["c"]):
                    continue
                reach = self.reach(a)
                opts = [s for s in free if s in reach]
                if opts:
                    s = min(opts, key=lambda s: reach[s])
                    if best is None or reach[s] < best[0]:
                        best = (reach[s], a["id"], s)
            if best is None:
                c = t.city(cid)
                if t.army_at(c["r"], c["c"]) or t.act("buildArmy", cityId=cid):
                    return None
                a = t.army_at(c["r"], c["c"])
                reach = self.reach(a)
                opts = [s for s in free if s in reach]
                if not opts:
                    return None
                best = (0, a["id"], min(opts, key=lambda s: reach[s]))
            _, aid, s = best
            if t.act("moveArmy", armyId=aid, r=s[0], c=s[1]):
                return None
        if commit:
            for a in t.armies():
                if hex_distance(a["r"], a["c"], city["r"], city["c"]) <= rng and not t.city_at(a["r"], a["c"]):
                    self.guard_ids.add(a["id"])
        mine = sum(1 for s in ring if t.army_at(*s) and t.army_at(*s)["owner"] == t.pi)
        threat = self.enemy_attack(t.city(cid), len(ring) - mine, full)
        pow0 = t.q("powerOf", "$S", t.pi)
        price = t.q("powerPrice", "$S", t.pi)
        coins = t.q("available", "$S", t.pi, "coins")
        need, holds = 0, True
        while t.q("defenseValue", "$S", {"$city": cid}, pow0 + need) <= threat:
            if (need + 1) * price > coins:
                holds = False
                break
            need += 1
        if need and t.act("buyPower", n=need):
            return None
        margin = t.q("defenseValue", "$S", {"$city": cid}, pow0 + need) - threat
        return holds, coins0 - coins + need * price, margin

    # ------------------------------------------------------------------ end of turn
    def leave_cities(self):
        t = self.t
        for a in t.armies():
            if not t.city_at(a["r"], a["c"]):
                continue
            reach = self.reach(a)
            if reach:
                s = min(reach, key=lambda s: (reach[s], s))
                t.act("moveArmy", armyId=a["id"], r=s[0], c=s[1])

    def spend_rest(self):
        t = self.t
        price = t.q("powerPrice", "$S", t.pi)
        n = t.q("available", "$S", t.pi, "coins") // max(price, 1)
        if n > 0:
            t.act("buyPower", n=n)
