#!/usr/bin/env python3
import os
import http.server
import socketserver
import socket
import threading
import json
import random
import time
import signal
from typing import Dict, List
import hashlib
import base64
import struct

# HTTP server configuration
HTTP_PORT = 8000

# Game constants
BOARD_WIDTH = 1000
BOARD_HEIGHT = 700
GRID_SIZE = 20
FOOD_COUNT_PER_PLAYER = 1


class Snake:
    def __init__(self, player_id: str, player_name: str, x: int, y: int):
        self.player_id = player_id
        self.player_name = player_name
        self.body = [(x, y)]
        self.direction = 'right'
        self.alive = True
        self.score = 0
        self.total_score = 0  # Total accumulated score across all games
        self.speed_boost_end = 0  # Timestamp when speed boost ends
        self.speed_reduction_end = 0  # Timestamp when speed reduction ends

    def move(self):
        if not self.alive:
            return

        head_x, head_y = self.body[0]

        if self.direction == 'up':
            new_head = (head_x, head_y - GRID_SIZE)
        elif self.direction == 'down':
            new_head = (head_x, head_y + GRID_SIZE)
        elif self.direction == 'left':
            new_head = (head_x - GRID_SIZE, head_y)
        elif self.direction == 'right':
            new_head = (head_x + GRID_SIZE, head_y)

        # Check wall collision
        if (new_head[0] < 0 or new_head[0] >= BOARD_WIDTH or
                new_head[1] < 0 or new_head[1] >= BOARD_HEIGHT):
            self.alive = False
            return

        self.body.insert(0, new_head)

    def grow(self):
        pass

    def grow_multiple(self, count):
        """Grow the snake by multiple segments"""
        for _ in range(count):
            if len(self.body) > 0:
                # Add segment at the tail position
                tail = self.body[-1]
                self.body.append(tail)

    def apply_speed_boost(self, duration_seconds=5):
        """Apply speed boost for specified duration"""
        self.speed_boost_end = time.time() + duration_seconds

    def apply_double_speed_boost(self, duration_seconds=5):
        """Apply double speed boost for specified duration"""
        self.speed_boost_end = time.time() + duration_seconds * 2

    def apply_speed_reduction(self, duration_seconds=5):
        """Apply 80% speed reduction for specified duration"""
        self.speed_reduction_end = time.time() + duration_seconds

    def reduce_length_by_half(self):
        """Reduce snake length by half (minimum 1 segment)"""
        if len(self.body) > 1:
            new_length = max(1, len(self.body) // 2)
            self.body = self.body[:new_length]

    def has_speed_boost(self):
        """Check if snake currently has speed boost"""
        return time.time() < self.speed_boost_end

    def has_speed_reduction(self):
        """Check if snake currently has speed reduction"""
        return time.time() < self.speed_reduction_end

    def shrink(self):
        if len(self.body) > 1:
            self.body.pop()

    def check_self_collision(self):
        head = self.body[0]
        return head in self.body[1:]

    def to_dict(self):
        return {
            'player_id': self.player_id,
            'player_name': self.player_name,
            'body': self.body,
            'alive': self.alive,
            'score': self.score,
            'total_score': self.total_score,
            'has_speed_boost': self.has_speed_boost(),
            'has_speed_reduction': self.has_speed_reduction()
        }


class GameState:
    def __init__(self):
        self.players: Dict[str, object] = {}
        self.snakes: Dict[str, Snake] = {}
        # Changed to list of dicts to include food type
        self.food: List[dict] = []
        # Deadly gold food with expiry time
        self.deadly_gold_food: List[dict] = []
        self.game_started = False
        self.game_running = False
        self.votes: Dict[str, bool] = {}  # Track player votes to start game
        self._lock = threading.Lock()  # Thread safety

    def add_player(self, websocket, player_id: str, player_name: str = ''):
        with self._lock:
            # Check if game is already running
            if self.game_running:
                return False  # Cannot join during active game

            self.players[player_id] = websocket
            x = random.randint(5, (BOARD_WIDTH // GRID_SIZE) - 5) * GRID_SIZE
            y = random.randint(5, (BOARD_HEIGHT // GRID_SIZE) - 5) * GRID_SIZE
            display_name = player_name or f"Player{len(self.snakes) + 1}"
            self.snakes[player_id] = Snake(player_id, display_name, x, y)
            self.votes[player_id] = False  # Initialize vote as False
            self.update_food_unsafe()
            return True

    def remove_player(self, player_id: str):
        with self._lock:
            if player_id in self.players:
                del self.players[player_id]
            if player_id in self.snakes:
                del self.snakes[player_id]
            if player_id in self.votes:
                del self.votes[player_id]
            self.update_food_unsafe()

    def vote_to_start(self, player_id: str):
        """Player votes to start the game"""
        with self._lock:
            if player_id in self.votes and not self.game_started:
                self.votes[player_id] = True
                return self.check_votes_ready()
            return False

    def check_votes_ready(self):
        """Check if all players have voted to start"""
        if len(self.players) < 1:
            return False
        # All players must vote to start
        return (all(self.votes.values()) and
                len(self.votes) == len(self.players))

    def get_vote_status(self):
        """Get current voting status"""
        total_players = len(self.players)
        voted_count = sum(1 for vote in self.votes.values() if vote)
        return {
            'total_players': total_players,
            'voted_count': voted_count,
            'votes_needed': total_players - voted_count
        }

    def update_food_unsafe(self):
        """Update food without locking - call only when already locked"""
        target_food_count = len(self.players) * FOOD_COUNT_PER_PLAYER
        while len(self.food) < target_food_count:
            x = random.randint(0, (BOARD_WIDTH // GRID_SIZE) - 1) * GRID_SIZE
            y = random.randint(0, (BOARD_HEIGHT // GRID_SIZE) - 1) * GRID_SIZE
            valid_position = True
            for snake in self.snakes.values():
                if (x, y) in snake.body:
                    valid_position = False
                    break

            # Check if position is already occupied by existing food
            position_occupied = False
            for food_item in self.food:
                if food_item['x'] == x and food_item['y'] == y:
                    position_occupied = True
                    break

            if valid_position and not position_occupied:
                # Determine food type: 16% normal, 16% white, 16% purple, 8% black, 16% gray, 8% gold, 20% yellow
                rand = random.random()
                if rand < 0.16:
                    food_type = 'normal'
                elif rand < 0.32:
                    food_type = 'white'  # +10 length
                elif rand < 0.48:
                    food_type = 'purple'  # speed boost 5s
                elif rand < 0.56:
                    food_type = 'black'  # +20 length
                elif rand < 0.72:
                    food_type = 'gray'  # reduce speed by 80% for 5s
                elif rand < 0.8:
                    food_type = 'gold'  # special gold food
                else:
                    food_type = 'yellow'  # spawn 50 random foods

                self.food.append({
                    'x': x,
                    'y': y,
                    'type': food_type
                })

    def create_multiple_food(self, count):
        """Create multiple random foods (excluding yellow food)"""
        for _ in range(count):
            attempts = 0
            while attempts < 50:  # Limit attempts to avoid infinite loop
                x = random.randint(
                    0, (BOARD_WIDTH // GRID_SIZE) - 1) * GRID_SIZE
                y = random.randint(
                    0, (BOARD_HEIGHT // GRID_SIZE) - 1) * GRID_SIZE

                # Check if position is valid (not on snakes or existing food)
                position_valid = True

                # Check snakes
                for snake in self.snakes.values():
                    if (x, y) in snake.body:
                        position_valid = False
                        break

                # Check existing food
                if position_valid:
                    for food_item in self.food:
                        if food_item['x'] == x and food_item['y'] == y:
                            position_valid = False
                            break

                # Check existing deadly gold food
                if position_valid:
                    for gold_food in self.deadly_gold_food:
                        if gold_food['x'] == x and gold_food['y'] == y:
                            position_valid = False
                            break

                if position_valid:
                    # Choose random food type (excluding yellow)
                    rand = random.random()
                    if rand < 0.25:
                        food_type = 'normal'
                    elif rand < 0.5:
                        food_type = 'white'
                    elif rand < 0.7:
                        food_type = 'purple'
                    elif rand < 0.8:
                        food_type = 'black'
                    elif rand < 0.95:
                        food_type = 'gray'
                    else:
                        food_type = 'gold'

                    self.food.append({
                        'x': x,
                        'y': y,
                        'type': food_type
                    })
                    break

                attempts += 1

    def create_deadly_gold_food(self, count):
        """Create deadly gold food that expires in 5 seconds"""
        current_time = time.time()
        for _ in range(count):
            attempts = 0
            while attempts < 50:  # Limit attempts to avoid infinite loop
                x = random.randint(
                    0, (BOARD_WIDTH // GRID_SIZE) - 1) * GRID_SIZE
                y = random.randint(
                    0, (BOARD_HEIGHT // GRID_SIZE) - 1) * GRID_SIZE

                # Check if position is valid (not on snakes or existing food)
                position_valid = True

                # Check snakes
                for snake in self.snakes.values():
                    if (x, y) in snake.body:
                        position_valid = False
                        break

                # Check existing food
                if position_valid:
                    for food_item in self.food:
                        if food_item['x'] == x and food_item['y'] == y:
                            position_valid = False
                            break

                # Check existing deadly gold food
                if position_valid:
                    for gold_food in self.deadly_gold_food:
                        if gold_food['x'] == x and gold_food['y'] == y:
                            position_valid = False
                            break

                if position_valid:
                    self.deadly_gold_food.append({
                        'x': x,
                        'y': y,
                        'type': 'deadly_gold',
                        'expires_at': current_time + 5  # Expires in 5 seconds
                    })
                    break

                attempts += 1

    def clean_expired_deadly_gold_food(self):
        """Remove expired deadly gold food"""
        current_time = time.time()
        self.deadly_gold_food = [
            food for food in self.deadly_gold_food
            if food['expires_at'] > current_time
        ]

    def update_food(self):
        with self._lock:
            self.update_food_unsafe()

    def start_game(self):
        with self._lock:
            if self.check_votes_ready() and not self.game_started:
                self.game_started = True
                self.game_running = True
                # Reset votes for next round
                for player_id in self.votes:
                    self.votes[player_id] = False
                return True
            return False

    def restart_game(self):
        with self._lock:
            self.game_started = False
            self.game_running = False
            # Reset all votes
            for player_id in self.votes:
                self.votes[player_id] = False
            for player_id, snake in self.snakes.items():
                # Save current score to total_score before reset
                snake.total_score += snake.score

                x = random.randint(
                    5, (BOARD_WIDTH // GRID_SIZE) - 5) * GRID_SIZE
                y = random.randint(
                    5, (BOARD_HEIGHT // GRID_SIZE) - 5) * GRID_SIZE
                snake.body = [(x, y)]
                snake.direction = 'right'
                snake.alive = True
                snake.score = 0
            self.food = []
            self.update_food_unsafe()

    def update_game(self):
        with self._lock:
            if not self.game_running:
                return

            for snake in self.snakes.values():
                snake.move()

            alive_snakes = [
                snake for snake in self.snakes.values() if snake.alive]

            # Head-to-head collisions
            head_collisions = []
            for i, snake1 in enumerate(alive_snakes):
                for j, snake2 in enumerate(alive_snakes[i+1:], i+1):
                    if snake1.body[0] == snake2.body[0]:
                        head_collisions.append((snake1, snake2))

            for snake1, snake2 in head_collisions:
                len1, len2 = len(snake1.body), len(snake2.body)
                if len1 > len2:
                    snake2.alive = False
                    snake1.score += 20
                elif len2 > len1:
                    snake1.alive = False
                    snake2.score += 20
                else:
                    snake1.alive = False
                    snake2.alive = False

            for snake in self.snakes.values():
                if not snake.alive:
                    continue
                head = snake.body[0]
                if snake.check_self_collision():
                    snake.alive = False
                    continue
                for other_snake in self.snakes.values():
                    if (other_snake.player_id != snake.player_id and
                            other_snake.alive and head in other_snake.body[1:]):
                        snake.alive = False
                        other_snake.score += 10
                        break

                # Check food collision
                eaten_food = None
                for food_item in self.food:
                    if head[0] == food_item['x'] and head[1] == food_item['y']:
                        eaten_food = food_item
                        break

                # Check deadly gold food collision
                # Use slice to avoid modification during iteration
                for gold_food in self.deadly_gold_food[:]:
                    if head[0] == gold_food['x'] and head[1] == gold_food['y']:
                        # Snake dies from eating deadly gold food
                        snake.alive = False
                        self.deadly_gold_food.remove(gold_food)
                        break

                if eaten_food:
                    self.food.remove(eaten_food)
                    if eaten_food['type'] == 'normal':
                        snake.grow()
                        snake.score += 10
                    elif eaten_food['type'] == 'white':
                        snake.grow_multiple(10)
                        snake.score += 50  # Bonus points for special food
                    elif eaten_food['type'] == 'purple':
                        snake.apply_speed_boost(5)
                        snake.score += 30  # Bonus points for special food
                    elif eaten_food['type'] == 'black':
                        snake.grow_multiple(20)
                        snake.score += 100  # Higher bonus for +20 length
                    elif eaten_food['type'] == 'gray':
                        snake.apply_speed_reduction(5)
                        snake.score -= 10  # Small penalty for speed reduction
                    elif eaten_food['type'] == 'gold':
                        # Gold food: creates deadly gold food pieces
                        player_count = len(
                            [s for s in self.snakes.values() if s.alive])
                        deadly_count = player_count * 20
                        self.create_deadly_gold_food(deadly_count)
                        snake.score += 200  # Big bonus for risky gold food
                    elif eaten_food['type'] == 'yellow':
                        # Yellow food: spawn 50 random foods (excluding yellow)
                        print(f"DEBUG: Yellow food eaten! Creating 50 foods...")
                        self.create_multiple_food(50)
                        snake.score += 150  # Good bonus for yellow food
                        print(
                            f"DEBUG: Total food count after yellow: {len(self.food)}")
                    self.update_food_unsafe()
                else:
                    snake.shrink()

            alive_count = sum(
                1 for snake in self.snakes.values() if snake.alive)
            if alive_count <= 1 and len(self.snakes) > 1:
                self.game_running = False

    def to_dict(self):
        with self._lock:
            vote_status = self.get_vote_status()
            return {
                'snakes': [snake.to_dict() for snake in self.snakes.values()],
                'food': self.food.copy(),
                'deadly_gold_food': self.deadly_gold_food.copy(),
                'game_started': self.game_started,
                'game_running': self.game_running,
                'player_count': len(self.players),
                'vote_status': vote_status,
                'votes': self.votes.copy()
            }


# Global game state
game_state = GameState()

# Global server reference for shutdown
httpd_server = None
game_thread = None


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    print("\n🛑 Shutting down server...")

    # Close all WebSocket connections
    for player_id, websocket in list(game_state.players.items()):
        try:
            # Send close frame to client
            websocket.send_close_frame()
        except Exception:
            pass

    # Clear all connections
    game_state.players.clear()
    game_state.snakes.clear()

    print("✅ Server stopped cleanly")
    # Force exit without waiting for server shutdown
    os._exit(0)


class WebSocketHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # Add CSP header to allow inline scripts and styles
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; "
                         "script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; "
                         "connect-src 'self' ws: wss:; "
                         "img-src 'self' data:;")
        super().end_headers()

    def do_GET(self):
        # Check if this is a WebSocket upgrade request
        if (self.headers.get('Upgrade', '').lower() == 'websocket' and
                self.headers.get('Connection', '').lower() == 'upgrade'):
            self.handle_websocket_upgrade()
        else:
            # Regular HTTP request
            super().do_GET()

    def handle_websocket_upgrade(self):
        try:
            # Get WebSocket key
            key = self.headers.get('Sec-WebSocket-Key')
            if not key:
                self.send_error(400, "Missing Sec-WebSocket-Key")
                return

            # Generate accept key
            magic = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            accept = base64.b64encode(
                hashlib.sha1((key + magic).encode()).digest()
            ).decode()

            # Send WebSocket handshake response
            self.send_response(101, 'Switching Protocols')
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()

            # Handle WebSocket connection
            self.handle_websocket_connection()

        except Exception as e:
            print(f"WebSocket handshake error: {e}")
            self.send_error(500, "WebSocket handshake failed")

    def handle_websocket_connection(self):
        player_id = f"player_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        player_name = ''
        print(f"DEBUG: New WebSocket connection, player_id: {player_id}")

        try:
            while True:
                try:
                    # Read WebSocket frame
                    message = self.read_websocket_frame()
                    if message is None:
                        break

                    data = json.loads(message)

                    if data['type'] == 'join':
                        player_name = data.get('name', 'Anonymous')
                        print(
                            f"DEBUG: Player joining with name: '{player_name}'")

                        # Try to add player
                        if game_state.add_player(self, player_id, player_name):
                            # Successfully joined
                            response = {
                                'type': 'init',
                                'player_id': player_id,
                                'game_state': game_state.to_dict()
                            }
                            print(
                                f"DEBUG: Sending init response to {player_name}")
                            self.send_websocket_message(json.dumps(response))
                            self.broadcast_game_state()
                            self.broadcast_system_message(
                                f"{player_name} đã tham gia game!")
                        else:
                            # Cannot join - game is running
                            response = {
                                'type': 'join_rejected',
                                'reason': 'Game đang chạy, không thể tham gia!'
                            }
                            self.send_websocket_message(json.dumps(response))
                            print(
                                f"DEBUG: Rejected {player_name} - game running")
                            break

                    elif data['type'] == 'start_game':
                        if game_state.start_game():
                            self.broadcast_game_state()
                            self.broadcast_system_message("Game đã bắt đầu!")
                        else:
                            # Not enough votes, send vote status
                            vote_status = game_state.get_vote_status()
                            self.send_websocket_message(json.dumps({
                                'type': 'vote_needed',
                                'vote_status': vote_status
                            }))

                    elif data['type'] == 'vote_start':
                        if game_state.vote_to_start(player_id):
                            # All votes collected, start game automatically
                            game_state.start_game()
                            self.broadcast_game_state()
                            self.broadcast_system_message(
                                "Tất cả đã vote! Game bắt đầu!")
                        else:
                            # Update vote status
                            self.broadcast_vote_status()
                            if player_id in game_state.snakes:
                                player_name = game_state.snakes[player_id].player_name
                                vote_status = game_state.get_vote_status()
                                self.broadcast_system_message(
                                    f"{player_name} đã vote để bắt đầu! "
                                    f"({vote_status['voted_count']}/{vote_status['total_players']})")

                    elif data['type'] == 'restart_game':
                        game_state.restart_game()
                        self.broadcast_game_state()
                        self.broadcast_system_message(
                            "Game đã được reset! Hãy vote để bắt đầu lại.")

                    elif data['type'] == 'chat':
                        message_text = data.get('message', '').strip()
                        if message_text and player_id in game_state.snakes:
                            snake = game_state.snakes[player_id]
                            self.broadcast_chat_message(
                                snake.player_name, message_text)

                    elif data['type'] == 'move' and player_id in game_state.snakes:
                        snake = game_state.snakes[player_id]
                        if snake.alive and game_state.game_running and len(snake.body) > 0:
                            direction = data['direction']
                            opposite = {
                                'up': 'down', 'down': 'up',
                                'left': 'right', 'right': 'left'
                            }

                            can_change_direction = True
                            if len(snake.body) > 1:
                                head_x, head_y = snake.body[0]
                                second_x, second_y = snake.body[1]
                                new_head = None
                                if direction == 'up':
                                    new_head = (head_x, head_y - GRID_SIZE)
                                elif direction == 'down':
                                    new_head = (head_x, head_y + GRID_SIZE)
                                elif direction == 'left':
                                    new_head = (head_x - GRID_SIZE, head_y)
                                elif direction == 'right':
                                    new_head = (head_x + GRID_SIZE, head_y)
                                if new_head == (second_x, second_y):
                                    can_change_direction = False

                            if (direction != opposite.get(snake.direction) and can_change_direction):
                                snake.direction = direction

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    print(f"WebSocket message error: {e}")
                    break

        except Exception as e:
            print(f"WebSocket connection error: {e}")
        finally:
            game_state.remove_player(player_id)
            self.broadcast_game_state()

    def read_websocket_frame(self):
        try:
            # Read first 2 bytes
            data = self.rfile.read(2)
            if len(data) != 2:
                return None

            fin = (data[0] & 0x80) >> 7
            opcode = data[0] & 0x0f
            masked = (data[1] & 0x80) >> 7
            payload_len = data[1] & 0x7f

            # Handle extended payload length
            if payload_len == 126:
                data = self.rfile.read(2)
                if len(data) != 2:
                    return None
                payload_len = struct.unpack('>H', data)[0]
            elif payload_len == 127:
                data = self.rfile.read(8)
                if len(data) != 8:
                    return None
                payload_len = struct.unpack('>Q', data)[0]

            # Read mask key
            if masked:
                mask = self.rfile.read(4)
                if len(mask) != 4:
                    return None

            # Read payload
            payload = self.rfile.read(payload_len)
            if len(payload) != payload_len:
                return None

            # Unmask payload
            if masked:
                payload = bytes(payload[i] ^ mask[i % 4]
                                for i in range(len(payload)))

            # Handle close frame
            if opcode == 0x8:
                print("DEBUG: WebSocket close frame received")
                return None

            # Only process text frames
            if opcode != 0x1:
                return None

            return payload.decode('utf-8')

        except UnicodeDecodeError as e:
            print(f"Unicode decode error: {e}")
            return None
        except Exception as e:
            print(f"Read frame error: {e}")
            return None

    def send_websocket_message(self, message):
        try:
            message_bytes = message.encode('utf-8')
            length = len(message_bytes)

            # Create frame
            frame = bytearray()
            frame.append(0x81)  # FIN=1, opcode=text

            if length <= 125:
                frame.append(length)
            elif length <= 65535:
                frame.append(126)
                frame.extend(struct.pack('>H', length))
            else:
                frame.append(127)
                frame.extend(struct.pack('>Q', length))

            frame.extend(message_bytes)
            self.wfile.write(frame)
            self.wfile.flush()

        except Exception as e:
            print(f"Send message error: {e}")

    def send_close_frame(self):
        """Send WebSocket close frame"""
        try:
            # Close frame: FIN=1, opcode=8, no payload
            frame = bytearray([0x88, 0x00])
            self.wfile.write(frame)
            self.wfile.flush()
        except:
            pass

    def broadcast_game_state(self):
        message = json.dumps({
            'type': 'game_state',
            'game_state': game_state.to_dict()
        })
        for player_id, websocket in list(game_state.players.items()):
            try:
                websocket.send_websocket_message(message)
            except:
                game_state.remove_player(player_id)

    def broadcast_chat_message(self, player_name: str, message_text: str):
        message = json.dumps({
            'type': 'chat',
            'player_name': player_name,
            'message': message_text
        })
        for player_id, websocket in list(game_state.players.items()):
            try:
                websocket.send_websocket_message(message)
            except:
                game_state.remove_player(player_id)

    def broadcast_system_message(self, message_text: str):
        message = json.dumps({
            'type': 'system',
            'message': message_text
        })
        for player_id, websocket in list(game_state.players.items()):
            try:
                websocket.send_websocket_message(message)
            except:
                game_state.remove_player(player_id)

    def broadcast_vote_status(self):
        """Broadcast current voting status to all players"""
        vote_status = game_state.get_vote_status()
        message = json.dumps({
            'type': 'vote_status',
            'vote_status': vote_status,
            'votes': game_state.votes.copy()
        })
        for player_id, websocket in list(game_state.players.items()):
            try:
                websocket.send_websocket_message(message)
            except:
                game_state.remove_player(player_id)


def get_local_ip():
    """Get the local IP address of this machine"""
    try:
        # Connect to a dummy address to get local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        return local_ip
    except Exception:
        return "localhost"


def start_combined_server():
    """Start combined HTTP and WebSocket server"""
    global httpd_server, game_thread

    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Enable SO_REUSEADDR to prevent "Address already in use" error
    socketserver.TCPServer.allow_reuse_address = True

    # Use ThreadingTCPServer to handle multiple connections simultaneously
    with socketserver.ThreadingTCPServer(("", HTTP_PORT), WebSocketHTTPRequestHandler) as httpd:
        httpd_server = httpd
        local_ip = get_local_ip()
        print("🐍 SNAKE GAME - COMBINED SERVER (THREADED)")
        print("=" * 50)
        print(f"🖥️  Server IP: {local_ip}")
        print(f"🎮 Game URL: http://{local_ip}:{HTTP_PORT}/index.html")
        print(f"🔌 WebSocket: ws://{local_ip}:{HTTP_PORT}")
        print("=" * 50)
        print("📋 SHARE WITH OTHERS:")
        print(f"   http://{local_ip}:{HTTP_PORT}/index.html")
        print("=" * 50)
        print("⚡ Server started! Multiple connections supported!")
        print("💡 Press Ctrl+C to stop")

        # Start game loop in a separate thread
        game_thread = threading.Thread(target=game_loop, daemon=True)
        game_thread.start()

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 Shutting down server...")

            # Close all WebSocket connections
            for player_id, websocket in list(game_state.players.items()):
                try:
                    websocket.send_close_frame()
                except Exception:
                    pass

            # Clear all connections
            game_state.players.clear()
            game_state.snakes.clear()

            print("✅ Server stopped cleanly")


def game_loop():
    """Game update loop"""
    tick_counter = 0
    while True:
        if game_state.game_running:
            tick_counter += 1

            # Clean expired deadly gold food
            with game_state._lock:
                game_state.clean_expired_deadly_gold_food()

            # Handle different speed updates
            with game_state._lock:
                for snake in game_state.snakes.values():
                    if not snake.alive:
                        continue

                    should_move = True

                    # Check if snake has speed reduction (80% slower, moves every 5 ticks)
                    if snake.has_speed_reduction() and tick_counter % 5 != 0:
                        should_move = False

                    if should_move:
                        snake.move()

                        # Check collisions
                        head = snake.body[0]
                        if snake.check_self_collision():
                            snake.alive = False
                            continue

                        for other_snake in game_state.snakes.values():
                            if (other_snake.player_id != snake.player_id and
                                    other_snake.alive and head in other_snake.body[1:]):
                                snake.alive = False
                                other_snake.score += 10
                                break

                        if not snake.alive:
                            continue

                        # Check food collision
                        eaten_food = None
                        for food_item in game_state.food:
                            if head[0] == food_item['x'] and head[1] == food_item['y']:
                                eaten_food = food_item
                                break

                        if eaten_food:
                            game_state.food.remove(eaten_food)
                            if eaten_food['type'] == 'normal':
                                snake.grow()
                                snake.score += 10
                            elif eaten_food['type'] == 'white':
                                snake.grow_multiple(10)
                                snake.score += 50
                            elif eaten_food['type'] == 'purple':
                                snake.apply_speed_boost(5)
                                snake.score += 30
                            elif eaten_food['type'] == 'black':
                                snake.grow_multiple(20)
                                snake.score += 100
                            elif eaten_food['type'] == 'gray':
                                snake.apply_speed_reduction(5)
                                snake.score -= 10
                            elif eaten_food['type'] == 'yellow':
                                # Yellow food: spawn 50 random foods (excluding yellow)
                                game_state.create_multiple_food(50)
                                snake.score += 150  # Good bonus for yellow food
                            game_state.update_food_unsafe()
                        else:
                            snake.shrink()

                    # Extra move for speed boosted snakes on even ticks
                    if snake.alive and snake.has_speed_boost() and tick_counter % 2 == 0:
                        snake.move()

                        # Check collisions for speed boost move
                        head = snake.body[0]
                        if snake.check_self_collision():
                            snake.alive = False
                            continue

                        for other_snake in game_state.snakes.values():
                            if (other_snake.player_id != snake.player_id and
                                    other_snake.alive and head in other_snake.body[1:]):
                                snake.alive = False
                                other_snake.score += 10
                                break

                        if not snake.alive:
                            continue

                        # Check food collision for speed boost move
                        eaten_food = None
                        for food_item in game_state.food:
                            if head[0] == food_item['x'] and head[1] == food_item['y']:
                                eaten_food = food_item
                                break

                        if eaten_food:
                            game_state.food.remove(eaten_food)
                            if eaten_food['type'] == 'normal':
                                snake.grow()
                                snake.score += 10
                            elif eaten_food['type'] == 'white':
                                snake.grow_multiple(10)
                                snake.score += 50
                            elif eaten_food['type'] == 'purple':
                                snake.apply_speed_boost(5)
                                snake.score += 30
                            elif eaten_food['type'] == 'black':
                                snake.grow_multiple(20)
                                snake.score += 100
                            elif eaten_food['type'] == 'gray':
                                snake.apply_speed_reduction(5)
                                snake.score -= 10
                            elif eaten_food['type'] == 'yellow':
                                # Yellow food: spawn 50 random foods (excluding yellow)
                                game_state.create_multiple_food(50)
                                snake.score += 150  # Good bonus for yellow food
                            game_state.update_food_unsafe()
                        else:
                            snake.shrink()

                # Check for game end
                alive_count = sum(
                    1 for snake in game_state.snakes.values() if snake.alive)
                if alive_count <= 1 and len(game_state.snakes) > 1:
                    # Save current scores to total_score when game ends
                    for snake in game_state.snakes.values():
                        snake.total_score += snake.score
                    game_state.game_running = False

            # Broadcast game state to all connected players
            message = json.dumps({
                'type': 'game_state',
                'game_state': game_state.to_dict()
            })
            for player_id, websocket in list(game_state.players.items()):
                try:
                    websocket.send_websocket_message(message)
                except:
                    game_state.remove_player(player_id)
        time.sleep(0.1)  # 100ms game tick


if __name__ == "__main__":
    start_combined_server()
