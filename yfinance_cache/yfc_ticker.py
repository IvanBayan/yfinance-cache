import yfinance as yf

from . import yfc_cache_manager as yfcm
from . import yfc_dat as yfcd
from . import yfc_utils as yfcu
from . import yfc_logging as yfcl
from . import yfc_time as yfct
from . import yfc_prices_manager as yfcp

import numpy as np
import pandas as pd
import datetime
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo
import os
import re
# from time import perf_counter

# TODO: Ticker: add method to delete ticker from cache


class Ticker:
    def __init__(self, ticker, session=None):
        self.ticker = ticker.upper()

        self.session = session
        self.dat = yf.Ticker(self.ticker, session=self.session)

        self._yf_lag = None

        self._histories_manager = None

        self._info = None
        self._fast_info = None

        self._splits = None

        self._shares = None

        self._financials = None
        self._quarterly_financials = None

        self._major_holders = None

        self._institutional_holders = None

        self._balance_sheet = None
        self._quarterly_balance_sheet = None

        self._cashflow = None
        self._quarterly_cashflow = None

        self._earnings = None
        self._quarterly_earnings = None

        self._sustainability = None

        self._recommendations = None

        self._calendar = None

        self._isin = None

        self._options = None

        self._news = None

        self._debug = False
        # self._debug = True

        self._tz = None
        self._exchange = None

    def history(self,
                interval="1d",
                max_age=None,  # defaults to half of interval
                period=None,
                start=None, end=None, prepost=False, actions=True,
                adjust_splits=True, adjust_divs=True,
                keepna=False,
                proxy=None, rounding=False,
                debug=True, quiet=False,
                trigger_at_market_close=False):

        # t0 = perf_counter()

        if prepost:
            raise Exception("pre and post-market caching currently not implemented. If you really need it raise an issue on Github")

        debug_yfc = self._debug
        # debug_yfc = True

        if start is not None or end is not None:
            log_msg = f"Ticker::history(tkr={self.ticker} interval={interval} start={start} end={end} max_age={max_age} trigger_at_market_close={trigger_at_market_close} adjust_splits={adjust_splits}, adjust_divs={adjust_divs})"
        else:
            log_msg = f"Ticker::history(tkr={self.ticker} interval={interval} period={period} max_age={max_age} trigger_at_market_close={trigger_at_market_close} adjust_splits={adjust_splits}, adjust_divs={adjust_divs})"
        yfcl.TraceEnter(log_msg)

        td_1d = datetime.timedelta(days=1)
        exchange, tz_name = self._getExchangeAndTz()
        tz_exchange = ZoneInfo(tz_name)
        yfct.SetExchangeTzName(exchange, tz_name)
        dt_now = pd.Timestamp.utcnow()

        # Type checks
        if max_age is not None:
            if isinstance(max_age, str):
                if max_age.endswith("wk"):
                    max_age = re.sub("wk$", "w", max_age)
                max_age = pd.Timedelta(max_age)
            if not isinstance(max_age, (datetime.timedelta, pd.Timedelta)):
                raise Exception("Argument 'max_age' must be Timedelta or equivalent string")
        if period is not None:
            if start is not None or end is not None:
                raise Exception("Don't set both 'period' and 'start'/'end'' arguments")
            if isinstance(period, str):
                if period in ["max", "ytd"]:
                    period = yfcd.periodStrToEnum[period]
                else:
                    if period.endswith("wk"):
                        period = re.sub("wk$", "w", period)
                    if period.endswith("y"):
                        period = relativedelta(years=int(re.sub("y$", "", period)))
                    elif period.endswith("mo"):
                        period = relativedelta(months=int(re.sub("mo", "", period)))
                    else:
                        period = pd.Timedelta(period)
            if not isinstance(period, (yfcd.Period, datetime.timedelta, pd.Timedelta, relativedelta)):
                raise Exception(f"Argument 'period' must be one of: 'max', 'ytd', Timedelta or equivalent string. Not {type(period)}")
        if isinstance(interval, str):
            if interval not in yfcd.intervalStrToEnum.keys():
                raise Exception("'interval' if str must be one of: {}".format(yfcd.intervalStrToEnum.keys()))
            interval = yfcd.intervalStrToEnum[interval]
        if not isinstance(interval, yfcd.Interval):
            raise Exception("'interval' must be yfcd.Interval")

        start_d = None ; end_d = None
        start_dt = None ; end_dt = None
        interday = interval in [yfcd.Interval.Days1, yfcd.Interval.Week, yfcd.Interval.Months1, yfcd.Interval.Months3]
        if start is not None:
            start_dt, start_d = self._process_user_dt(start)
            if start_dt > dt_now:
                return None
            if interval == yfcd.Interval.Week:
                # Note: if start is on weekend then Yahoo can return weekly data starting
                #       on Saturday. This breaks YFC, start must be Monday! So fix here:
                if start_dt is None:
                    # Working with simple dates, easy
                    if start_d.weekday() in [5, 6]:
                        start_d += datetime.timedelta(days=7-start_d.weekday())
                else:
                    wd = start_d.weekday()
                    if wd in [5, 6]:
                        start_d += datetime.timedelta(days=7-wd)
                        start_dt = datetime.datetime.combine(start_d, datetime.time(0), tz_exchange)

        if end is not None:
            end_dt, end_d = self._process_user_dt(end)

        if start_dt is not None and end_dt is not None and start_dt >= end_dt:
            raise ValueError("start must be < end")

        if debug_yfc:
            print("- start_dt={} , end_dt={}".format(start_dt, end_dt))

        if (start_dt is not None) and start_dt == end_dt:
            return None

        if max_age is None:
            if interval == yfcd.Interval.Days1:
                max_age = datetime.timedelta(hours=4)
            elif interval == yfcd.Interval.Week:
                max_age = datetime.timedelta(hours=60)
            elif interval == yfcd.Interval.Months1:
                max_age = datetime.timedelta(days=15)
            elif interval == yfcd.Interval.Months3:
                max_age = datetime.timedelta(days=45)
            else:
                max_age = 0.5*yfcd.intervalToTimedelta[interval]
            if start is not None:
                max_age = min(max_age, dt_now-start_dt)

        if period is not None:
            if isinstance(period, (datetime.timedelta, pd.Timedelta)):
                if (dt_now - max_age) < (dt_now - period):
                    raise Exception(f"max_age={max_age} must be less than period={period}")
            elif period == yfcd.Period.Ytd:
                dt_now_ex = dt_now.tz_convert(tz_exchange)
                dt_year_start = pd.Timestamp(year=dt_now_ex.year, month=1, day=1).tz_localize(tz_exchange)
                if (dt_now - max_age) < dt_year_start:
                    raise Exception(f"max_age={max_age} must be less than days since this year start")
        elif start is not None:
            if (dt_now - max_age) < start_dt:
                raise Exception(f"max_age={max_age} must be closer to now than start={start}")


        if start_dt is not None:
            try:
                sched_14d = yfct.GetExchangeSchedule(exchange, start_dt.date(), start_dt.date()+14*td_1d)
            except Exception as e:
                if "Need to add mapping" in str(e):
                    raise Exception("Need to add mapping of exchange {} to xcal (ticker={})".format(exchange, self.ticker))
                else:
                    raise
            if sched_14d is None:
                raise Exception("sched_14d is None for date range {}->{} and ticker {}".format(start_dt.date(), start_dt.date()+14*td_1d, self.ticker))
            if sched_14d["open"].iloc[0] > dt_now:
                # Requested date range is in future
                return None
        else:
            sched_14d = None

        # All date checks passed so can begin fetching

        if ((start_d is None) or (end_d is None)) and (start_dt is not None) and (end_dt is not None):
            # if start_d/end_d not set then start/end are datetimes, so need to inspect
            # schedule opens/closes to determine days
            if sched_14d is not None:
                sched = sched_14d.iloc[0:1]
            else:
                sched = yfct.GetExchangeSchedule(exchange, start_dt.date(), end_dt.date()+td_1d)
            n = sched.shape[0]
            start_d = start_dt.date() if start_dt < sched["open"].iloc[0] else start_dt.date()+td_1d
            end_d = end_dt.date()+td_1d if end_dt >= sched["close"].iloc[n-1] else end_dt.date()

        if self._histories_manager is None:
            self._histories_manager = yfcp.HistoriesManager(self.ticker, exchange, tz_name, self.session, proxy)

        # t1_setup = perf_counter()

        hist = self._histories_manager.GetHistory(interval)
        if period is not None:
            h = hist.get(start=None, end=None, period=period, max_age=max_age, trigger_at_market_close=trigger_at_market_close, quiet=quiet)
        elif interday:
            h = hist.get(start_d, end_d, period=None, max_age=max_age, trigger_at_market_close=trigger_at_market_close, quiet=quiet)
        else:
            h = hist.get(start_dt, end_dt, period=None, max_age=max_age, trigger_at_market_close=trigger_at_market_close, quiet=quiet)
        if (h is None) or h.shape[0] == 0:
            msg = f"YFC: history() exiting without price data (tkr={self.ticker}"
            if start_dt is not None or end_dt is not None:
                msg += f" start_dt={start_dt} end_dt={end_dt}"
            else:
                msg += f" period={period}"
            msg += f" max_age={max_age}"
            msg += f" interval={yfcd.intervalToString[interval]})"
            raise Exception(msg)

        # t2_sync = perf_counter()

        f_dups = h.index.duplicated()
        if f_dups.any():
            raise Exception("{}: These timepoints have been duplicated: {}".format(self.ticker, h.index[f_dups]))

        # Present table for user:
        h_copied = False
        if (start_dt is not None) and (end_dt is not None):
            h = h.loc[start_dt:end_dt-datetime.timedelta(milliseconds=1)].copy()
            h_copied = True

        if not keepna:
            price_data_cols = [c for c in yfcd.yf_data_cols if c in h.columns]
            mask_nan_or_zero = (np.isnan(h[price_data_cols].to_numpy()) | (h[price_data_cols].to_numpy() == 0)).all(axis=1)
            if mask_nan_or_zero.any():
                h = h.drop(h.index[mask_nan_or_zero])
                h_copied = True
        # t3_filter = perf_counter()

        if h.shape[0] == 0:
            h = None
        else:
            if adjust_splits:
                if not h_copied:
                    h = h.copy()
                for c in ["Open", "Close", "Low", "High", "Dividends"]:
                    h[c] = np.multiply(h[c].to_numpy(), h["CSF"].to_numpy())
                h["Volume"] = np.round(np.divide(h["Volume"].to_numpy(), h["CSF"].to_numpy()), 0).astype('int')
            if adjust_divs:
                if not h_copied:
                    h = h.copy()
                for c in ["Open", "Close", "Low", "High"]:
                    h[c] = np.multiply(h[c].to_numpy(), h["CDF"].to_numpy())
            else:
                if not h_copied:
                    h = h.copy()
                h["Adj Close"] = np.multiply(h["Close"].to_numpy(), h["CDF"].to_numpy())
            h = h.drop(["CSF", "CDF"], axis=1)

            if rounding:
                # Round to 4 sig-figs
                if not h_copied:
                    h = h.copy()
                f_na = h["Close"].isna()
                na = f_na.any()
                if na:
                    f_nna = ~f_na
                    if not f_nna.any():
                        raise Exception(f"{self.ticker}: price table is entirely NaNs. Delisted?" +" \n" + log_msg)
                    last_close = h["Close"][f_nna].iloc[-1]
                else:
                    last_close = h["Close"].iloc[-1]
                rnd = yfcu.CalculateRounding(last_close, 4)
                for c in ["Open", "Close", "Low", "High"]:
                    if na:
                        h.loc[f_nna, c] = np.round(h.loc[f_nna, c].to_numpy(), rnd)
                    else:
                        h[c] = np.round(h[c].to_numpy(), rnd)

            if debug_yfc:
                print("- h:")
                cols = [c for c in ["Close", "Dividends", "Volume", "CDF", "CSF"] if c in h.columns]
                print(h[cols])
                if "Dividends" in h.columns:
                    f = h["Dividends"] != 0.0
                    if f.any():
                        print("- dividends:")
                        print(h.loc[f, cols])
                print("")
            yfcl.TraceExit("Ticker::history() returning")

        # t4_adjust = perf_counter()
        # t_setup = t1_setup - t0
        # t_sync = t2_sync - t1_setup
        # t_filter = t3_filter - t2_sync
        # t_adjust = t4_adjust - t3_filter
        # t_sum = t_setup + t_sync + t_filter + t_adjust
        # print("TIME: {:.4f}s: setup={:.4f} sync={:.4f} filter={:.4f} adjust={:.4f}".format(t_sum, t_setup, t_sync, t_filter, t_adjust))
        # t_setup *= 100/t_sum
        # t_sync *= 100/t_sum
        # t_cache *= 100/t_sum
        # t_filter *= 100/t_sum
        # t_adjust *= 100/t_sum
        # print("TIME %:        setup={:.1f}%  sync={:.1f}%  filter={:.1f}%  adjust={:.1f}%".format(t_setup, t_sync, t_filter, t_adju

        return h

    def _getCachedPrices(self, interval, proxy=None):
        if self._histories_manager is None:
            exchange, tz_name = self._getExchangeAndTz()
            self._histories_manager = yfcp.HistoriesManager(self.ticker, exchange, tz_name, self.session, proxy)

        if isinstance(interval, str):
            if interval not in yfcd.intervalStrToEnum.keys():
                raise Exception("'interval' if str must be one of: {}".format(yfcd.intervalStrToEnum.keys()))
            interval = yfcd.intervalStrToEnum[interval]

        return self._histories_manager.GetHistory(interval).h

    def _getExchangeAndTz(self):
        if self._tz is not None and self._exchange is not None:
            return self._tz, self._exchange

        exchange, tz_name = None, None
        try:
            exchange = self.get_info('9999d')['exchange']
            if "exchangeTimezoneName" in self.get_info('9999d'):
                tz_name = self.get_info('9999d')["exchangeTimezoneName"]
            else:
                tz_name = self.get_info('9999d')["timeZoneFullName"]
        except Exception:
            md = yf.Ticker(self.ticker, session=self.session).history_metadata
            if 'exchangeName' in md.keys():
                exchange = md['exchangeName']
            if 'exchangeTimezoneName' in md.keys():
                tz_name = md['exchangeTimezoneName']

        if exchange is None or tz_name is None:
            raise Exception(f"{self.ticker}: exchange and timezone not available")
        self._tz = tz_name
        self._exchange = exchange
        return self._tz, self._exchange

    def verify_cached_prices(self, rtol=0.0001, vol_rtol=0.005, correct=False, discard_old=False, quiet=True, debug=False, debug_interval=None):
        if debug:
            quiet = False
        if debug_interval is not None and isinstance(debug_interval, str):
            debug_interval = yfcd.intervalStrToEnum[debug_interval]

        fn_locals = locals()
        del fn_locals["self"]

        interval = yfcd.Interval.Days1
        cache_key = "history-"+yfcd.intervalToString[interval]
        if not yfcm.IsDatumCached(self.ticker, cache_key):
            return True

        yfcl.TraceEnter(f"Ticker::verify_cached_prices(tkr={self.ticker} {fn_locals})")

        if self._histories_manager is None:
            exchange, tz_name = self._getExchangeAndTz()
            self._histories_manager = yfcp.HistoriesManager(self.ticker, exchange, tz_name, self.session, proxy=None)

        v = True

        # First verify 1d
        dt0 = self._histories_manager.GetHistory(interval)._getCachedPrices().index[0]
        self.history(start=dt0.date(), quiet=quiet, trigger_at_market_close=True)  # ensure have all dividends
        v = self._verify_cached_prices_interval(interval, rtol, vol_rtol, correct, discard_old, quiet, debug)
        if debug_interval == yfcd.Interval.Days1:
            yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {v} (1st pass)")
            return v
        if not v:
            if debug or not correct:
                yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {v} (1st pass)")
                return v
        if correct:
            # Rows were removed so re-fetch. Only do for 1d data
            self.history(start=dt0.date(), quiet=quiet)

            # repeat verification, because 'fetch backporting' may be buggy
            v2 = self._verify_cached_prices_interval(interval, rtol, vol_rtol, correct, discard_old, quiet, debug)
            if not v2 and debug:
                yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {v2} (post-correction)")
                return v2
            if not v2:
                yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {v2} (post-correction)")
                return v2

            if not v:
                # Stop after correcting first problem, because user won't have been shown the next problem yet
                yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {v} (corrected but user should review next problem)")
                return v

        if debug_interval is not None:
            if debug_interval == yfcd.Interval.Days1:
                intervals = []
            else:
                intervals = [debug_interval]
            debug = True
        else:
            intervals = yfcd.Interval
        for interval in intervals:
            if interval == yfcd.Interval.Days1:
                continue
            istr = yfcd.intervalToString[interval]
            cache_key = "history-"+istr
            if not yfcm.IsDatumCached(self.ticker, cache_key):
                continue
            vi = self._verify_cached_prices_interval(interval, rtol, vol_rtol, correct, discard_old, quiet, debug)
            yfcl.TracePrint(f"{istr}: vi={vi}")

            if not vi and correct:
                # Stop after correcting first problem, because user won't have been shown the next problem yet
                yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {vi}")
                return vi

            v = v and vi

        yfcl.TraceExit(f"Ticker::verify_cached_prices() returning {v}")

        return v

    def _verify_cached_prices_interval(self, interval, rtol=0.0001, vol_rtol=0.005, correct=False, discard_old=False, quiet=True, debug=False):
        if debug:
            quiet = False

        fn_locals = locals()
        del fn_locals["self"]

        if isinstance(interval, str):
            if interval not in yfcd.intervalStrToEnum.keys():
                raise Exception("'interval' if str must be one of: {}".format(yfcd.intervalStrToEnum.keys()))
            interval = yfcd.intervalStrToEnum[interval]

        istr = yfcd.intervalToString[interval]
        cache_key = "history-"+istr
        if not yfcm.IsDatumCached(self.ticker, cache_key):
            return True

        yfcl.TraceEnter(f"Ticker::_verify_cached_prices_interval(tkr={self.ticker}, {fn_locals})")

        if self._histories_manager is None:
            exchange, tz_name = self._getExchangeAndTz()
            self._histories_manager = yfcp.HistoriesManager(self.ticker, exchange, tz_name, self.session, proxy=None)

        v = self._histories_manager.GetHistory(interval)._verifyCachedPrices(rtol, vol_rtol, correct, discard_old, quiet, debug)

        yfcl.TraceExit(f"Ticker::_verify_cached_prices_interval() returning {v}")
        return v

    def _process_user_dt(self, dt):
        d = None
        exchange, tz_name = self._getExchangeAndTz()
        tz_exchange = ZoneInfo(tz_name)
        if isinstance(dt, str):
            d = datetime.datetime.strptime(dt, "%Y-%m-%d").date()
            dt = datetime.datetime.combine(d, datetime.time(0), tz_exchange)
        elif isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
            d = dt
            dt = datetime.datetime.combine(dt, datetime.time(0), tz_exchange)
        elif not isinstance(dt, datetime.datetime):
            raise Exception("Argument 'dt' must be str, date or datetime")
        dt = dt.replace(tzinfo=tz_exchange) if dt.tzinfo is None else dt.astimezone(tz_exchange)

        if d is None and dt.time() == datetime.time(0):
            d = dt.date()

        return dt, d

    @property
    def info(self):
        return self.get_info()

    def get_info(self, max_age=None):
        if self._info is not None:
            return self._info

        if max_age is None:
            max_age = pd.Timedelta(yfcm._option_manager.max_ages.info)
        elif not isinstance(max_age, (datetime.timedelta, pd.Timedelta)):
            max_age = pd.Timedelta(max_age)
        if max_age < pd.Timedelta(0):
            raise Exception(f"'max_age' must be positive timedelta not {max_age}")

        if yfcm.IsDatumCached(self.ticker, "info"):
            self._info, md = yfcm.ReadCacheDatum(self.ticker, "info", True)
            if 'FetchDate' not in self._info.keys():
                fp = yfcm.GetFilepath(self.ticker, 'info')
                mod_dt = datetime.datetime.fromtimestamp(os.path.getmtime(fp))
                self._info['FetchDate'] = mod_dt
                yfcm.WriteCacheMetadata(self.ticker, "info", 'LastCheck', mod_dt)

        if self._info is not None:
            if md is None:
                md = {}
            if not 'LastCheck' in md.keys():
                md['LastCheck'] = self._info['FetchDate']
                yfcm.WriteCacheMetadata(self.ticker, "info", 'LastCheck', md['LastCheck'])
            if max(self._info['FetchDate'], md['LastCheck']) + max_age > pd.Timestamp.now():
                return self._info

        i = self.dat.info
        i['FetchDate'] = pd.Timestamp.now()

        if self._info is not None:
            # Check new info is not downgrade
            diff = len(i) - len(self._info)
            diff_pct = float(diff) / float(len(self._info))
            if diff_pct < -0.1 and diff < -10:
                msg = 'When fetching new info, significant amount of data has disappeared\n'
                missing_keys = [k for k in self._info.keys() if k not in i.keys()]
                new_keys = [k for k in i.keys() if k not in self._info.keys()]
                msg += "- missing: "
                msg += str({k:self._info[k] for k in missing_keys}) + '\n'
                msg += "- new: "
                msg += str({k:i[k] for k in new_keys}) + '\n'

                # msg += "\nKeep new data?"
                # keep = click.confirm(msg, default=False)
                # if not keep:
                #     return self._info
                #
                msg += "\nDiscarding fetched info."
                print(f'{self.ticker}: {msg}')
                yfcm.WriteCacheMetadata(self.ticker, "info", 'LastCheck', i['FetchDate'])
                return self._info

        self._info = i
        yfcm.StoreCacheDatum(self.ticker, "info", self._info)

        exchange, tz_name = self._getExchangeAndTz()
        yfct.SetExchangeTzName(exchange, tz_name)

        return self._info

    @property
    def fast_info(self):
        if self._fast_info is not None:
            return self._fast_info

        if yfcm.IsDatumCached(self.ticker, "fast_info"):
            try:
                self._fast_info = yfcm.ReadCacheDatum(self.ticker, "fast_info")
            except Exception:
                pass
            else:
                return self._fast_info

        # self._fast_info = self.dat.fast_info
        self._fast_info = {}
        for k in self.dat.fast_info.keys():
            try:
                self._fast_info[k] = self.dat.fast_info[k]
            except Exception as e:
                if "decrypt" in str(e):
                    pass
                else:
                    print(f"TICKER = {self.ticker}")
                    raise
        yfcm.StoreCacheDatum(self.ticker, "fast_info", self._fast_info)

        yfct.SetExchangeTzName(self._fast_info["exchange"], self._fast_info["timezone"])

        return self._fast_info

    @property
    def splits(self):
        if self._splits is not None:
            return self._splits

        if yfcm.IsDatumCached(self.ticker, "splits"):
            self._splits = yfcm.ReadCacheDatum(self.ticker, "splits")
            return self._splits

        self._splits = self.dat.splits
        yfcm.StoreCacheDatum(self.ticker, "splits", self._splits)
        return self._splits


    def get_shares(self, start=None, end=None, max_age='30d'):
        debug = False
        # debug = True

        max_age = pd.Timedelta(max_age)

        # Process dates
        exchange, tz = self._getExchangeAndTz()
        dt_now = pd.Timestamp.utcnow().tz_convert(tz)
        if start is not None:
            start_dt, start_d = self._process_user_dt(start)
            start = start_d
        if end is not None:
            end_dt, end_d = self._process_user_dt(end)
            end = end_d
        if end is None:
            end_dt = dt_now
            end = dt_now.date()
        if start is None:
            start = end - pd.Timedelta(days=548)  # 18 months
        if start >= end:
            raise Exception("Start date must be before end")
        if debug:
            print("- start =", start, " end =", end)

        if self._shares is None:
            if yfcm.IsDatumCached(self.ticker, "shares"):
                if debug:
                    print("- init shares from cache")
                self._shares = yfcm.ReadCacheDatum(self.ticker, "shares")
            else:
                if debug:
                    print("- fetching shares")
                self._shares = self._fetch_shares(start, end)
                yfcm.StoreCacheDatum(self.ticker, "shares", self._shares)
                # return self._shares
                f_na = self._shares['Shares'].isna()
                return self._shares[~f_na]

        if debug:
            print("- self._shares:", self._shares.index[0], '->', self._shares.index[-1])

        td_1d = datetime.timedelta(days=1)
        last_row = self._shares.iloc[-1]
        if pd.isna(last_row['Shares']):# and last_row['FetchDate'].date() == last_row.name:
            if debug:
                print("- dropping last row from cached")
            self._shares = self._shares.drop(self._shares.index[-1])

        if not isinstance(self._shares.index, pd.DatetimeIndex):
            self._shares.index = pd.to_datetime(self._shares.index).tz_localize(tz)
        if self._shares['Shares'].dtype == 'float':
            # Convert to Int, and add a little to avoid rounding errors
            self._shares['Shares'] = (self._shares['Shares']+0.01).round().astype('Int64')

        if start < self._shares.index[0].date():
            df_pre = self._fetch_shares(start, self._shares.index[0])
            if df_pre is not None:
                self._shares = pd.concat([df_pre, self._shares])
        if (end-td_1d) > self._shares.index[-1].date() and \
            (end - self._shares.index[-1].date()) > max_age:
            df_post = self._fetch_shares(self._shares.index[-1] + td_1d, end)
            if df_post is not None:
                self._shares = pd.concat([self._shares, df_post])

        self._shares = self._shares
        yfcm.StoreCacheDatum(self.ticker, "shares", self._shares)

        f_na = self._shares['Shares'].isna()
        shares = self._shares[~f_na]
        i0 = np.searchsorted(shares.index, start_dt)
        i1 = np.searchsorted(shares.index, end_dt)
        return shares.iloc[i0:i1]

    def _fetch_shares(self, start, end):
        td_1d = datetime.timedelta(days=1)

        exchange, tz = self._getExchangeAndTz()
        if isinstance(end, datetime.datetime):
            end_dt = end
            end_d = end.date()
        else:
            end_dt = pd.Timestamp(end).tz_localize(tz)
            end_d = end
        if isinstance(start, datetime.datetime):
            start_dt = start
            start_d = start.date()
        else:
            start_dt = pd.Timestamp(start).tz_localize(tz)
            start_d = start

        end_d = min(end_d, datetime.date.today() + td_1d)

        df = self.dat.get_shares_full(start_d, end_d)
        if df is None:
            return df
        if df.empty:
            return None

        # Convert to Pandas Int for NaN support
        df = df.astype('Int64')

        # Currently, yfinance uses ceil(end), so fix:
        if df.index[-1].date() == end_d:
            df.drop(df.index[-1])
            if df.empty:
                return None

        fetch_dt = pd.Timestamp.utcnow().tz_convert(tz)
        df = pd.DataFrame(df, columns=['Shares'])

        if start_d < df.index[0].date():
            df.loc[start_dt] = np.nan
        if (end_d-td_1d) > df.index[-1].date():
            df.loc[end_dt] = np.nan
        df = df.sort_index()

        df['FetchDate'] = fetch_dt

        return df

    @property
    def financials(self):
        if self._financials is not None:
            return self._financials

        if yfcm.IsDatumCached(self.ticker, "financials"):
            self._financials = yfcm.ReadCacheDatum(self.ticker, "financials")
            return self._financials

        self._financials = self.dat.financials
        yfcm.StoreCacheDatum(self.ticker, "financials", self._financials)
        return self._financials

    @property
    def quarterly_financials(self):
        if self._quarterly_financials is not None:
            return self._quarterly_financials

        if yfcm.IsDatumCached(self.ticker, "quarterly_financials"):
            self._quarterly_financials = yfcm.ReadCacheDatum(self.ticker, "quarterly_financials")
            return self._quarterly_financials

        self._quarterly_financials = self.dat.quarterly_financials
        yfcm.StoreCacheDatum(self.ticker, "quarterly_financials", self._quarterly_financials)
        return self._quarterly_financials

    @property
    def major_holders(self):
        if self._major_holders is not None:
            return self._major_holders

        if yfcm.IsDatumCached(self.ticker, "major_holders"):
            self._major_holders = yfcm.ReadCacheDatum(self.ticker, "major_holders")
            return self._major_holders

        self._major_holders = self.dat.major_holders
        yfcm.StoreCacheDatum(self.ticker, "major_holders", self._major_holders)
        return self._major_holders

    @property
    def institutional_holders(self):
        if self._institutional_holders is not None:
            return self._institutional_holders

        if yfcm.IsDatumCached(self.ticker, "institutional_holders"):
            self._institutional_holders = yfcm.ReadCacheDatum(self.ticker, "institutional_holders")
            return self._institutional_holders

        self._institutional_holders = self.dat.institutional_holders
        yfcm.StoreCacheDatum(self.ticker, "institutional_holders", self._institutional_holders)
        return self._institutional_holders

    @property
    def balance_sheet(self):
        if self._balance_sheet is not None:
            return self._balance_sheet

        if yfcm.IsDatumCached(self.ticker, "balance_sheet"):
            self._balance_sheet = yfcm.ReadCacheDatum(self.ticker, "balance_sheet")
            return self._balance_sheet

        self._balance_sheet = self.dat.balance_sheet
        yfcm.StoreCacheDatum(self.ticker, "balance_sheet", self._balance_sheet)
        return self._balance_sheet

    @property
    def quarterly_balance_sheet(self):
        if self._quarterly_balance_sheet is not None:
            return self._quarterly_balance_sheet

        if yfcm.IsDatumCached(self.ticker, "quarterly_balance_sheet"):
            self._quarterly_balance_sheet = yfcm.ReadCacheDatum(self.ticker, "quarterly_balance_sheet")
            return self._quarterly_balance_sheet

        self._quarterly_balance_sheet = self.dat.quarterly_balance_sheet
        yfcm.StoreCacheDatum(self.ticker, "quarterly_balance_sheet", self._quarterly_balance_sheet)
        return self._quarterly_balance_sheet

    @property
    def cashflow(self):
        if self._cashflow is not None:
            return self._cashflow

        if yfcm.IsDatumCached(self.ticker, "cashflow"):
            self._cashflow = yfcm.ReadCacheDatum(self.ticker, "cashflow")
            return self._cashflow

        self._cashflow = self.dat.cashflow
        yfcm.StoreCacheDatum(self.ticker, "cashflow", self._cashflow)
        return self._cashflow

    @property
    def quarterly_cashflow(self):
        if self._quarterly_cashflow is not None:
            return self._quarterly_cashflow

        if yfcm.IsDatumCached(self.ticker, "quarterly_cashflow"):
            self._quarterly_cashflow = yfcm.ReadCacheDatum(self.ticker, "quarterly_cashflow")
            return self._quarterly_cashflow

        self._quarterly_cashflow = self.dat.quarterly_cashflow
        yfcm.StoreCacheDatum(self.ticker, "quarterly_cashflow", self._quarterly_cashflow)
        return self._quarterly_cashflow

    @property
    def earnings(self):
        if self._earnings is not None:
            return self._earnings

        if yfcm.IsDatumCached(self.ticker, "earnings"):
            self._earnings = yfcm.ReadCacheDatum(self.ticker, "earnings")
            return self._earnings

        self._earnings = self.dat.earnings
        yfcm.StoreCacheDatum(self.ticker, "earnings", self._earnings)
        return self._earnings

    @property
    def quarterly_earnings(self):
        if self._quarterly_earnings is not None:
            return self._quarterly_earnings

        if yfcm.IsDatumCached(self.ticker, "quarterly_earnings"):
            self._quarterly_earnings = yfcm.ReadCacheDatum(self.ticker, "quarterly_earnings")
            return self._quarterly_earnings

        self._quarterly_earnings = self.dat.quarterly_earnings
        yfcm.StoreCacheDatum(self.ticker, "quarterly_earnings", self._quarterly_earnings)
        return self._quarterly_earnings

    @property
    def sustainability(self):
        if self._sustainability is not None:
            return self._sustainability

        if yfcm.IsDatumCached(self.ticker, "sustainability"):
            self._sustainability = yfcm.ReadCacheDatum(self.ticker, "sustainability")
            return self._sustainability

        self._sustainability = self.dat.sustainability
        yfcm.StoreCacheDatum(self.ticker, "sustainability", self._sustainability)
        return self._sustainability

    @property
    def recommendations(self):
        if self._recommendations is not None:
            return self._recommendations

        if yfcm.IsDatumCached(self.ticker, "recommendations"):
            self._recommendations = yfcm.ReadCacheDatum(self.ticker, "recommendations")
            return self._recommendations

        self._recommendations = self.dat.recommendations
        yfcm.StoreCacheDatum(self.ticker, "recommendations", self._recommendations)
        return self._recommendations

    @property
    def calendar(self):
        max_age = pd.Timedelta(yfcm._option_manager.max_ages.calendar)

        if self._calendar is None:
            if yfcm.IsDatumCached(self.ticker, "calendar"):
                self._calendar = yfcm.ReadCacheDatum(self.ticker, "calendar")
                if 'FetchDate' not in self._calendar.keys():
                    fp = yfcm.GetFilepath(self.ticker, 'info')
                    mod_dt = datetime.datetime.fromtimestamp(os.path.getmtime(fp))
                    self._calendar['FetchDate'] = mod_dt

        if (self._calendar is not None) and (self._calendar['FetchDate'] + max_age) > pd.Timestamp.now():
            return self._calendar

        c = self.dat.calendar
        c['FetchDate'] = pd.Timestamp.now()
        
        if self._calendar is not None:
            # Check calendar info is not downgrade
            diff = len(c) - len(self._calendar)
            if diff < -1:
                # More than 1 element disappeared
                msg = 'When fetching new calendar, data has disappeared\n'
                msg += '- cached calendar:\n'
                msg += f'{self._calendar}' + '\n'
                msg += '- new calendar:\n'
                msg += f'{c}' + '\n'
                raise Exception(msg)

        yfcm.StoreCacheDatum(self.ticker, "calendar", c)
        self._calendar = c
        return self._calendar

    @property
    def inin(self):
        if self._inin is not None:
            return self._inin

        if yfcm.IsDatumCached(self.ticker, "inin"):
            self._inin = yfcm.ReadCacheDatum(self.ticker, "inin")
            return self._inin

        self._inin = self.dat.inin
        yfcm.StoreCacheDatum(self.ticker, "inin", self._inin)
        return self._inin

    @property
    def options(self):
        if self._options is not None:
            return self._options

        if yfcm.IsDatumCached(self.ticker, "options"):
            self._options = yfcm.ReadCacheDatum(self.ticker, "options")
            return self._options

        self._options = self.dat.options
        yfcm.StoreCacheDatum(self.ticker, "options", self._options)
        return self._options

    @property
    def news(self):
        if self._news is not None:
            return self._news

        if yfcm.IsDatumCached(self.ticker, "news"):
            self._news = yfcm.ReadCacheDatum(self.ticker, "news")
            return self._news

        self._news = self.dat.news
        yfcm.StoreCacheDatum(self.ticker, "news", self._news)
        return self._news

    @property
    def yf_lag(self):
        if self._yf_lag is not None:
            return self._yf_lag

        exchange, tz_name = self._getExchangeAndTz()
        exchange_str = "exchange-{0}".format(exchange)
        if yfcm.IsDatumCached(exchange_str, "yf_lag"):
            self._yf_lag = yfcm.ReadCacheDatum(exchange_str, "yf_lag")
            if self._yf_lag:
                return self._yf_lag

        # Just use specified lag
        specified_lag = yfcd.exchangeToYfLag[exchange]
        self._yf_lag = specified_lag
        return self._yf_lag


def verify_cached_tickers_prices(session=None, rtol=0.0001, vol_rtol=0.005, correct=False, halt_on_fail=True, resume_from_tkr=None, debug_tkr=None, debug_interval=None):
    """
    :Parameters:
        session:
            Recommend providing a 'requests_cache' session, in case
            you have to abort and resume verification (likely).
        resume_from_tkr: str
            Resume verification from this ticker (alphabetical order).
            Because maybe you had to abort verification partway.
        debug_tkr: str
            Only verify this ticker.
            Because maybe you want to investigate a difference.
    """

    if debug_interval is not None and isinstance(debug_interval, str):
        if debug_interval not in yfcd.intervalStrToEnum.keys():
            raise Exception("'debug_interval' if str must be one of: {}".format(yfcd.intervalStrToEnum.keys()))
        debug_interval = yfcd.intervalStrToEnum[debug_interval]

    d = yfcm.GetCacheDirpath()
    tkrs = [x for x in os.listdir(d) if not x.startswith("exchange-") and os.path.isdir(os.path.join(d, x)) and '_' not in x]
    # tkrs = tkrs[:5]
    # tkrs = tkrs[:20]
    # tkrs = tkrs[tkrs.index("DDOG"):]

    debug = debug_tkr is not None
    if debug_tkr is not None:
        debug_tkr = debug_tkr.upper()
        tkrs = [debug_tkr]
    else:
        tkrs = sorted(tkrs)
        if resume_from_tkr is not None:
            resume_from_tkr = resume_from_tkr.upper()
            resume_after_tkr = False
            if resume_from_tkr.endswith("+1"):
                resume_after_tkr = True
                resume_from_tkr = resume_from_tkr.replace("+1", "")
            i = np.searchsorted(np.array(tkrs), resume_from_tkr, side="left")
            if resume_after_tkr:
                i += 1
            tkrs = tkrs[i:]

    if debug_tkr is not None:
        tkrs = [debug_tkr]
    tqdm_loaded = False
    try:
        from tqdm import tqdm
        t = tqdm(range(len(tkrs)))
        tqdm_loaded = True
    except ModuleNotFoundError:
        print("Install Python module 'tqdm' to print progress bar + estimated time to completion")
        t = range(len(tkrs))
    for i in t:
        tkr = tkrs[i]
        if tqdm_loaded:
            t.set_description("Verifying " + tkr)
        else:
            print(f"{tkr} : {i+1}/{len(tkrs)}")

        dat = Ticker(tkr, session=session)

        try:
            v = dat.verify_cached_prices(rtol=rtol, vol_rtol=vol_rtol, correct=correct, discard_old=correct, quiet=not debug, debug=debug, debug_interval=debug_interval)
        except yfcd.NoPriceDataInRangeException as e:
            print(str(e) + " - is it delisted? Aborting verification so you can investigate.")
            return
        if debug:
            return

        if correct:
            v = dat.verify_cached_prices(rtol=rtol, vol_rtol=vol_rtol, correct=correct, discard_old=False, quiet=not debug, debug=debug, debug_interval=debug_interval)

        if not v:
            v = dat.verify_cached_prices(rtol=rtol, vol_rtol=vol_rtol, correct=False, discard_old=False, quiet=False, debug=True, debug_interval=debug_interval)
            if halt_on_fail:
                raise Exception(f"{tkr}: verify failing")
            else:
                print(f"{tkr}: verify failing")
