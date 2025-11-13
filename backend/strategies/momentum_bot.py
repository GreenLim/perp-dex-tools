"""
Momentum Trading Bot - Follows price momentum using stop-limit orders

This bot implements a momentum trading strategy that avoids "catching falling knives"
by using conditional (stop-limit) orders that trigger only when price moves in the
desired direction. After entry, it immediately places a take-profit order at a
configurable percentage above entry price.

Key Features:
- Uses stop-limit orders instead of passive limit orders
- Follows momentum rather than mean reversion
- Immediate take-profit order placement after fill
- Multi-exchange support via ExchangeFactory
- Configurable trading parameters
- Telegram notifications for trade events
"""

import os
import time
import asyncio
import traceback
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from datetime import datetime

from exchanges import ExchangeFactory
from helpers import TradingLogger
from helpers.telegram_bot import TelegramBot


@dataclass
class MomentumConfig:
    """Configuration for momentum trading bot."""

    # Market configuration
    ticker: str                      # e.g., "ETH"
    exchange: str                    # Exchange name (e.g., "extended")

    # Order size
    quantity: Decimal                # Order size per trade

    # Entry strategy
    direction: str                   # "buy" or "sell" - trading direction
    tick_offset: int                 # Number of ticks to offset from best price (e.g., 1 means 1 tick away)

    # Exit strategy (take-profit)
    take_profit_pct: Decimal        # Take profit percentage (e.g., 0.02 for 0.02%)

    # Trading controls
    max_positions: int = 1          # Maximum concurrent positions
    wait_time: int = 5              # Wait time between checks (seconds)

    # Auto-filled fields (will be set during initialization)
    contract_id: Optional[str] = None       # Will be fetched from exchange
    tick_size: Optional[Decimal] = None     # Will be fetched from exchange

    @property
    def close_order_side(self) -> str:
        """Get the close order side based on bot direction."""
        return 'sell' if self.direction == "buy" else 'buy'


@dataclass
class PositionState:
    """Track current position state."""
    entry_order_id: Optional[str] = None
    entry_price: Optional[Decimal] = None
    entry_quantity: Decimal = Decimal('0')
    exit_order_id: Optional[str] = None
    is_position_open: bool = False
    has_embedded_tp: bool = False  # True if entry order includes embedded TP

    def reset(self):
        """Reset position state."""
        self.entry_order_id = None
        self.entry_price = None
        self.entry_quantity = Decimal('0')
        self.exit_order_id = None
        self.is_position_open = False
        self.has_embedded_tp = False


class MomentumBot:
    """
    Momentum Trading Bot - Main trading logic

    This bot uses stop-limit orders to enter positions only when price moves in the
    desired direction (momentum), then immediately places take-profit orders.

    Flow:
    1. Place stop-limit order at configured stop_price/limit_price
    2. Monitor order via WebSocket for fill
    3. On fill: immediately place take-profit limit order
    4. Monitor take-profit order until filled
    5. Repeat cycle
    """

    def __init__(self, config: MomentumConfig):
        """Initialize momentum trading bot."""
        self.config = config
        self.logger = TradingLogger(config.exchange, config.ticker, log_to_console=True)

        # Create exchange client using factory pattern
        try:
            self.exchange_client = ExchangeFactory.create_exchange(
                config.exchange,
                config
            )
        except ValueError as e:
            raise ValueError(f"Failed to create exchange client: {e}")

        # Telegram configuration
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')

        # Position tracking
        self.position = PositionState()

        # Trading state
        self.shutdown_requested = False
        self.loop = None

        # Market info (will be initialized)
        self.market_initialized = False

        # Events for async coordination
        self.entry_filled_event = asyncio.Event()
        self.exit_filled_event = asyncio.Event()

        # Setup WebSocket handlers
        self._setup_websocket_handlers()

    async def initialize_market_info(self):
        """Initialize market information from exchange."""
        if self.market_initialized:
            return

        self.logger.log("📊 Initializing market information...", "INFO")

        # Call get_contract_attributes() - this is the standard way
        # All exchanges should implement this method
        contract_id, tick_size = await self.exchange_client.get_contract_attributes()

        self.config.contract_id = contract_id
        self.config.tick_size = tick_size

        self.logger.log(f"✅ Contract ID: {self.config.contract_id}", "INFO")
        self.logger.log(f"✅ Tick Size: {self.config.tick_size}", "INFO")

        self.market_initialized = True
        self.logger.log("✅ Market information initialized", "INFO")

    async def get_best_price(self) -> Decimal:
        """
        Get the best price from the market.

        For buy orders: get best ask (lowest sell price)
        For sell orders: get best bid (highest buy price)

        Returns:
            Decimal: Best price in the market
        """
        try:
            # Use the exchange client's fetch_bbo_prices method
            if hasattr(self.exchange_client, 'fetch_bbo_prices'):
                best_bid, best_ask = await self.exchange_client.fetch_bbo_prices(self.config.contract_id)

                if self.config.direction == "buy":
                    # For buy, we want the best ask (lowest sell price)
                    if best_ask > 0:
                        self.logger.log(f"📊 Best ask: {best_ask}", "INFO")
                        return best_ask
                else:
                    # For sell, we want the best bid (highest buy price)
                    if best_bid > 0:
                        self.logger.log(f"📊 Best bid: {best_bid}", "INFO")
                        return best_bid

            raise ValueError("Unable to get best price from orderbook")

        except Exception as e:
            self.logger.log(f"❌ Error getting best price: {e}", "ERROR")
            raise

    def calculate_entry_prices(self, best_price: Decimal) -> tuple[Decimal, Decimal]:
        """
        Calculate stop price and limit price based on best price and tick offset.

        For buy orders:
            - stop_price = best_price + (tick_offset * tick_size)
            - limit_price = stop_price (same as stop price)

        For sell orders:
            - stop_price = best_price - (tick_offset * tick_size)
            - limit_price = stop_price (same as stop price)

        Args:
            best_price: Current best price in the market

        Returns:
            tuple: (stop_price, limit_price)
        """
        # Calculate offset in price terms
        price_offset = Decimal(str(self.config.tick_offset)) * self.config.tick_size

        if self.config.direction == "buy":
            # For buy: place order tick_offset ticks ABOVE best ask
            stop_price = best_price + price_offset
        else:
            # For sell: place order tick_offset ticks BELOW best bid
            stop_price = best_price - price_offset

        # For momentum bot, limit price = stop price (we want immediate fill when triggered)
        limit_price = stop_price

        # Round to tick size (should already be aligned, but ensure it)
        stop_price = self.exchange_client.round_to_tick(stop_price)
        limit_price = self.exchange_client.round_to_tick(limit_price)

        self.logger.log(
            f"💰 Entry prices calculated: stop={stop_price}, limit={limit_price} "
            f"(offset={self.config.tick_offset} ticks = {price_offset} from best={best_price})",
            "INFO"
        )

        return stop_price, limit_price

    def _setup_websocket_handlers(self):
        """Setup WebSocket handlers for order updates."""
        def order_update_handler(message):
            """Handle order updates from WebSocket."""
            try:
                # Check if this is for our contract
                if message.get('contract_id') != self.config.contract_id:
                    return

                order_id = message.get('order_id')
                status = message.get('status')
                side = message.get('side', '')
                filled_size = Decimal(message.get('filled_size', 0))
                price = Decimal(message.get('price', 0))

                # Log order update
                self.logger.log(
                    f"[{side.upper()}] [{order_id}] {status} "
                    f"{message.get('size')} @ {price}",
                    "INFO"
                )

                # Handle entry order fill
                if order_id == self.position.entry_order_id and status == 'FILLED':
                    self.position.entry_price = price
                    self.position.entry_quantity = filled_size
                    self.position.is_position_open = True

                    # Signal entry filled
                    if self.loop is not None:
                        self.loop.call_soon_threadsafe(self.entry_filled_event.set)

                    self.logger.log(f"✅ Entry order filled: {filled_size} @ {price}", "INFO")
                    self.logger.log_transaction(order_id, side, filled_size, price, status)

                # Handle exit order fill
                elif order_id == self.position.exit_order_id and status == 'FILLED':
                    # Signal exit filled
                    if self.loop is not None:
                        self.loop.call_soon_threadsafe(self.exit_filled_event.set)

                    self.logger.log(f"✅ Exit order filled: {filled_size} @ {price}", "INFO")
                    self.logger.log_transaction(order_id, side, filled_size, price, status)

                # Handle partial fills
                elif status == 'PARTIALLY_FILLED':
                    self.logger.log(f"⏳ Partial fill: {filled_size} @ {price}", "INFO")

            except Exception as e:
                self.logger.log(f"Error handling order update: {e}", "ERROR")
                self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

        # Register handler with exchange client
        self.exchange_client.setup_order_update_handler(order_update_handler)

    def send_telegram_notification(self, message: str):
        """Send notification via Telegram."""
        if not self.telegram_token or not self.telegram_chat_id:
            return

        try:
            with TelegramBot(self.telegram_token, self.telegram_chat_id) as tg_bot:
                tg_bot.send_text(message)
        except Exception as e:
            self.logger.log(f"Failed to send Telegram notification: {e}", "ERROR")

    async def graceful_shutdown(self, reason: str = "Unknown"):
        """Perform graceful shutdown of the trading bot."""
        self.logger.log(f"🛑 Starting graceful shutdown: {reason}", "INFO")
        self.shutdown_requested = True

        try:
            # Cancel any open orders
            if self.position.entry_order_id and not self.position.is_position_open:
                await self.exchange_client.cancel_order(self.position.entry_order_id)

            # Disconnect from exchange
            await self.exchange_client.disconnect()
            self.logger.log("✅ Graceful shutdown completed", "INFO")

            # Send shutdown notification
            shutdown_msg = (
                f"🛑 <b>Momentum Bot Shutdown</b>\n\n"
                f"Ticker: <code>{self.config.ticker}</code>\n"
                f"Reason: {reason}\n"
                f"Position Open: {self.position.is_position_open}"
            )
            self.send_telegram_notification(shutdown_msg)

        except Exception as e:
            self.logger.log(f"Error during graceful shutdown: {e}", "ERROR")

    async def place_entry_order(self) -> bool:
        """
        Place conditional entry order with take profit.

        Flow:
        1. Get best price from market
        2. Calculate trigger price based on tick_offset
        3. Calculate take profit price
        4. Place conditional order with TP

        Returns:
            bool: True if order placed successfully, False otherwise
        """
        try:
            # Get current best price
            best_price = await self.get_best_price()

            # Calculate trigger price (entry price)
            trigger_price, _ = self.calculate_entry_prices(best_price)

            # Calculate take profit price
            if self.config.direction == "buy":
                # For long: TP is above entry
                tp_price = trigger_price * (Decimal('1') + self.config.take_profit_pct / Decimal('100'))
            else:
                # For short: TP is below entry
                tp_price = trigger_price * (Decimal('1') - self.config.take_profit_pct / Decimal('100'))

            tp_price = self.exchange_client.round_to_tick(tp_price)

            self.logger.log(
                f"📊 Placing {self.config.direction} conditional order: "
                f"trigger={trigger_price}, TP={tp_price}, qty={self.config.quantity}",
                "INFO"
            )

            # Reset events
            self.entry_filled_event.clear()

            # Place conditional order with take profit
            # Check if exchange client supports conditional orders
            if hasattr(self.exchange_client, 'place_conditional_order'):
                order_result = await self.exchange_client.place_conditional_order(
                    self.config.contract_id,
                    self.config.quantity,
                    trigger_price,
                    self.config.direction,
                    take_profit_price=tp_price
                )
            else:
                # Fallback to regular order
                self.logger.log("⚠️ Exchange doesn't support conditional orders, using regular order", "WARNING")
                order_result = await self.exchange_client.place_open_order(
                    self.config.contract_id,
                    self.config.quantity,
                    self.config.direction
                )

            if not order_result.success:
                self.logger.log(f"❌ Failed to place entry order: {order_result.error_message}", "ERROR")
                return False

            self.position.entry_order_id = order_result.order_id
            # Store the expected entry price for TP calculation
            self.position.entry_price = trigger_price
            # Store whether TP was embedded in the entry order
            self.position.has_embedded_tp = order_result.has_embedded_tp

            self.logger.log(
                f"✅ Entry order placed: {order_result.order_id} "
                f"(trigger={trigger_price}, TP={tp_price}, embedded_tp={order_result.has_embedded_tp})",
                "INFO"
            )

            return True

        except Exception as e:
            self.logger.log(f"❌ Error placing entry order: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return False

    async def place_exit_order(self) -> bool:
        """
        Place take-profit conditional order after entry fill.

        Calculates take-profit price based on entry_price and take_profit_pct,
        then places a conditional order that triggers when price reaches TP level.

        Returns:
            bool: True if order placed successfully, False otherwise
        """
        try:
            # NOTE: Embedded TP is currently disabled, always place separate TP order
            # if self.position.has_embedded_tp: ...

            if not self.position.entry_price:
                self.logger.log("❌ Cannot place exit order: no entry price", "ERROR")
                return False

            # Calculate take-profit price
            if self.config.direction == "buy":
                # For long: sell at higher price (when price goes UP to TP)
                exit_price = self.position.entry_price * (
                    Decimal('1') + self.config.take_profit_pct / Decimal('100')
                )
            else:
                # For short: buy at lower price (when price goes DOWN to TP)
                exit_price = self.position.entry_price * (
                    Decimal('1') - self.config.take_profit_pct / Decimal('100')
                )

            # Round to tick size
            exit_price = self.exchange_client.round_to_tick(exit_price)

            self.logger.log(
                f"📊 Placing {self.config.close_order_side} take-profit LIMIT order: "
                f"price={exit_price}, qty={self.position.entry_quantity}",
                "INFO"
            )

            # Reset exit event
            self.exit_filled_event.clear()

            # Use regular limit order for take-profit
            # This is more reliable than embedded TP or conditional TP orders
            order_result = await self.exchange_client.place_close_order(
                self.config.contract_id,
                self.position.entry_quantity,
                exit_price,
                self.config.close_order_side
            )

            if not order_result.success:
                self.logger.log(f"❌ Failed to place exit order: {order_result.error_message}", "ERROR")
                return False

            self.position.exit_order_id = order_result.order_id
            self.logger.log(f"✅ Exit order placed: {order_result.order_id}", "INFO")

            # Send Telegram notification
            trade_msg = (
                f"🚀 <b>Momentum Bot - Position Opened</b>\n\n"
                f"Ticker: <code>{self.config.ticker}</code>\n"
                f"Direction: <b>{self.config.direction.upper()}</b>\n"
                f"Entry Price: <code>{self.position.entry_price}</code>\n"
                f"Exit Price: <code>{exit_price}</code>\n"
                f"Quantity: <code>{self.position.entry_quantity}</code>\n"
                f"Take Profit: <b>{self.config.take_profit_pct}%</b>"
            )
            self.send_telegram_notification(trade_msg)

            return True

        except Exception as e:
            self.logger.log(f"❌ Error placing exit order: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            return False

    async def run_trading_cycle(self):
        """
        Main trading cycle.

        Flow:
        1. Place entry stop-limit order
        2. Wait for fill
        3. Place take-profit order
        4. Wait for fill
        5. Reset and repeat
        """
        try:
            # Step 1: Place entry order
            if not await self.place_entry_order():
                self.logger.log("⚠️ Failed to place entry order, retrying...", "WARN")
                await asyncio.sleep(self.config.wait_time)
                return

            # Step 2: Wait for entry fill
            self.logger.log("⏳ Waiting for entry order fill...", "INFO")
            try:
                await asyncio.wait_for(self.entry_filled_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                self.logger.log("⏰ Entry order timeout, canceling...", "WARN")
                if self.position.entry_order_id:
                    await self.exchange_client.cancel_order(self.position.entry_order_id)
                self.position.reset()
                return

            # Step 3: Place exit order
            if not await self.place_exit_order():
                self.logger.log("❌ Failed to place exit order", "ERROR")
                # TODO: Handle error - may need to manually close position
                self.position.reset()
                return

            # Step 4: Wait for exit fill
            self.logger.log("⏳ Waiting for exit order fill...", "INFO")
            try:
                await asyncio.wait_for(self.exit_filled_event.wait(), timeout=3600)
            except asyncio.TimeoutError:
                self.logger.log("⏰ Exit order timeout", "WARN")
                # Exit order will remain open on exchange

            # Step 5: Reset position state
            self.logger.log("✅ Trading cycle completed", "INFO")

            # Send completion notification
            completion_msg = (
                f"✅ <b>Momentum Bot - Position Closed</b>\n\n"
                f"Ticker: <code>{self.config.ticker}</code>\n"
                f"Entry: <code>{self.position.entry_price}</code>\n"
                f"Quantity: <code>{self.position.entry_quantity}</code>"
            )
            self.send_telegram_notification(completion_msg)

            self.position.reset()

        except Exception as e:
            self.logger.log(f"❌ Error in trading cycle: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")

            # Send error notification
            error_msg = (
                f"❌ <b>Momentum Bot Error</b>\n\n"
                f"Ticker: <code>{self.config.ticker}</code>\n"
                f"Error: {str(e)[:200]}"
            )
            self.send_telegram_notification(error_msg)

            self.position.reset()

    async def run(self):
        """
        Main run loop for momentum bot.

        Connects to exchange and runs trading cycles continuously
        until shutdown is requested.
        """
        try:
            # Store event loop reference for thread-safe operations
            self.loop = asyncio.get_event_loop()

            # Connect to exchange
            self.logger.log("🔌 Connecting to exchange...", "INFO")
            await self.exchange_client.connect()
            self.logger.log("✅ Connected to exchange", "INFO")

            # Initialize market information
            await self.initialize_market_info()

            # Wait for orderbook to be ready
            self.logger.log("⏳ Waiting for orderbook data...", "INFO")
            await asyncio.sleep(3)

            # Send startup notification
            startup_msg = (
                f"🤖 <b>Momentum Bot Started</b>\n\n"
                f"Exchange: <code>{self.config.exchange}</code>\n"
                f"Ticker: <code>{self.config.ticker}</code>\n"
                f"Contract ID: <code>{self.config.contract_id}</code>\n"
                f"Direction: <b>{self.config.direction.upper()}</b>\n"
                f"Quantity: <code>{self.config.quantity}</code>\n"
                f"Tick Offset: <b>{self.config.tick_offset} ticks</b>\n"
                f"Take Profit: <b>{self.config.take_profit_pct}%</b>"
            )
            self.send_telegram_notification(startup_msg)

            # Main trading loop
            while not self.shutdown_requested:
                await self.run_trading_cycle()

                # Wait before next cycle
                if not self.shutdown_requested:
                    await asyncio.sleep(self.config.wait_time)

        except Exception as e:
            self.logger.log(f"❌ Fatal error in run loop: {e}", "ERROR")
            self.logger.log(f"Traceback: {traceback.format_exc()}", "ERROR")
            raise

        finally:
            # Cleanup
            await self.graceful_shutdown("Run loop terminated")


async def main():
    """
    Example usage of MomentumBot.

    This function demonstrates how to configure and run the momentum bot.
    In production, configuration should be loaded from environment variables
    or a config file.
    """
    # Example configuration for ETH momentum trading
    config = MomentumConfig(
        ticker="ETH",
        exchange="extended",
        quantity=Decimal("0.1"),
        direction="buy",
        tick_offset=1,  # Entry 1 tick away from current best price
        take_profit_pct=Decimal("0.02"),   # Take profit at 0.02%
        max_positions=1,
        wait_time=5
    )

    bot = MomentumBot(config)

    try:
        await bot.run()
    except KeyboardInterrupt:
        print("\n🛑 Keyboard interrupt received")
        await bot.graceful_shutdown("Keyboard interrupt")


if __name__ == "__main__":
    asyncio.run(main())
