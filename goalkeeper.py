import enum
import logging
import math
import multiprocessing as mp
import pickle
import queue
import time
from typing import Tuple, List, Optional

import click
import cv2 as cv
import numpy as np
import simple_pid

import measure
import robomaster as rm
from robomaster import CTX

rm.LOG_LEVEL = logging.DEBUG
pickle.DEFAULT_PROTOCOL = pickle.HIGHEST_PROTOCOL

GREEN_LOWER = (29, 90, 90)
GREEN_UPPER = (64, 255, 255)
BALL_ACTUAL_RADIUS = 0.065 / 2

QUEUE_TIMEOUT: float = 0.5
QUEUE_SIZE: int = 10
SYSTEM_FREQUENCY: int = 30


@enum.unique
class KeeperState(enum.IntEnum):
    _BEGIN = 0
    WATCHING = 1
    CHASING = 2
    KICKING = 3
    _END = 4

    def next(self):
        next_value = self + 1
        if next_value >= self._END:
            return KeeperState(self._BEGIN + 1)
        return KeeperState(next_value)

    def origin(self):
        return KeeperState(self._BEGIN + 1)


class KeeperMind(rm.Worker):
    MAX_EVENT_LAPSE: float = 20 / 1000.0  # in seconds
    DEFAULT_XY_SPEED: float = 0.4
    DEFAULT_Z_SPEED: float = 36.0
    BALL_ABSENT_TIMEOUT: float = 8.0
    KICK_TIMEOUT: float = 0.3
    CHASE_ENTER_FORWARD_THRESHOLD: float = 1.2
    CHASE_EXIT_FORWARD_THRESHOLD: float = 1.4
    DEGREE_EPS: float = 2.0  # in degrees
    DISTANCE_EPS: float = 0.01  # in meters
    SLEEP_SECONDS: float = 1.0
    GRAPH_SIZE: int = 1000

    def __init__(self, name: str, ip: str,
                 vision: mp.Queue, push: mp.Queue, event: mp.Queue,
                 field_width: float, field_depth: float, timeout: float = 10):
        super().__init__(name, None, None, (ip, 0), timeout, True)
        self._state: KeeperState = KeeperState.WATCHING
        self._max_y = field_width / 2.0
        self._max_x = field_depth / 2.0
        self._vision = vision
        self._push = push
        self._event = event
        self._y_pid: simple_pid.PID = simple_pid.PID(-10, -0.05, -0.5, setpoint=0, sample_time=1.0 / SYSTEM_FREQUENCY, output_limits=(-self.DEFAULT_XY_SPEED, self.DEFAULT_XY_SPEED))

        if field_width > field_depth:
            self._graph_pixel_size: float = 0.8 * self.GRAPH_SIZE / field_width  # pixel per meter
        else:
            self._graph_pixel_size: float = 0.8 * self.GRAPH_SIZE / field_depth  # pixel per meter
        self._graph_chassis_width = self._graph_pixel_size * measure.INFANTRY_WIDTH
        self._graph_chassis_length = self._graph_pixel_size * measure.INFANTRY_LENGTH
        self._graph_ball_radius = int(BALL_ACTUAL_RADIUS * self._graph_pixel_size)
        self._graph_base = np.zeros((self.GRAPH_SIZE, self.GRAPH_SIZE), dtype=np.uint8)
        cv.rectangle(self._graph_base, self._graph_offset(-0.8 * 0.5 * field_width, -0.8 * 0.5 * field_depth), self._graph_offset(0.8 * 0.5 * field_width, 0.8 * 0.5 * field_depth), (255, 0, 0), 4)

        # dynamic states
        self._position: rm.ChassisPosition = rm.ChassisPosition(0, 0, 0)
        self._position_last_seen: Optional[float] = None
        self._ball_distances: Optional[Tuple[float, float, float]] = None
        self._ball_last_seen: Optional[float] = None
        self._armor_hit_id: Optional[int] = None
        self._armor_hit_last_seen: Optional[float] = None

        self._cmd = rm.Commander(ip, timeout)
        self._cmd.robot_mode(rm.MODE_CHASSIS_LEAD)
        self._cmd.gimbal_moveto(pitch=-10)

        self._init_state()

    def _graph_offset(self, x: float, y: float) -> Tuple[int, int]:
        center = 0.5 * self.GRAPH_SIZE
        return int(center + x), int(center + y)

    def close(self):
        self._cmd.close()
        super().close()

    def _next_state(self):
        self._state: KeeperState = self._state.next()
        self._init_state()

    def _reset_state(self):
        if self._state == KeeperState.WATCHING:
            return
        self._state: KeeperState = self._state.origin()
        self._init_state()

    def _init_state(self):
        if self._state == KeeperState.WATCHING:
            self._recenter_to_field()
            self._cmd.led_control(rm.LED_ALL, rm.LED_EFFECT_PULSE, 0, 255, 0)
        elif self._state == KeeperState.CHASING:
            self._y_pid.reset()
            self._cmd.led_control(rm.LED_ALL, rm.LED_EFFECT_SOLID, 255, 0, 0)
        elif self._state == KeeperState.KICKING:
            self._cmd.chassis_move(-self._max_x * 2 / 3, speed_xy=self.DEFAULT_XY_SPEED)
            self._cmd.led_control(rm.LED_ALL, rm.LED_EFFECT_SOLID, 255, 255, 255)
        else:
            raise ValueError(f'unknown state {self._state}')

    def _drain_vision(self):
        updated: bool = False
        while not self.closed:
            try:
                self._ball_distances = self._vision.get_nowait()
                updated = True
            except queue.Empty:
                if updated:
                    self._ball_last_seen = time.time()
                return

    def _drain_push(self):
        updated: bool = False
        while not self.closed:
            try:
                push = self._push.get_nowait()
                updated = True
                if type(push) == rm.ChassisPosition:
                    self._position.x, self._position.y = push.x, push.y
                elif type(push) == rm.ChassisAttitude:
                    self._position.z = push.yaw
                else:
                    raise ValueError(f'unexpected push content: {push}')
            except queue.Empty:
                if updated:
                    self._position_last_seen = time.time()
                return

    def _drain_event(self):
        updated: bool = False
        while not self.closed:
            try:
                hit = self._event.get_nowait()
                updated = True
                if type(hit) == rm.ArmorHitEvent:
                    self._armor_hit_id = hit.index
                else:
                    raise ValueError(f'unexpected event content: {hit}')
            except queue.Empty:
                if updated:
                    self._ball_last_seen = time.time()
                return

    def _recenter_to_field(self):
        diff_z = 0 if math.fabs(self._position.z) < self.DEGREE_EPS else self._position.z
        self._cmd.chassis_move(-self._position.x, -self._position.y, -diff_z, speed_xy=self.DEFAULT_XY_SPEED, speed_z=self.DEFAULT_Z_SPEED)

    def _watch(self):
        if self._ball_distances is None:
            return
        forward = self._ball_distances[0]
        if forward < self.CHASE_ENTER_FORWARD_THRESHOLD:
            self._next_state()

    def _chase_kick_check(self) -> bool:
        # hit events
        if self._armor_hit_id is not None:
            self._cmd.chassis_wheel(0, 0, 0, 0)

            if self._armor_hit_id == 2:
                if self._state == KeeperState.KICKING:
                    time.sleep(self.SLEEP_SECONDS)
                self._next_state()
                return False

            time.sleep(self.SLEEP_SECONDS)
            self._reset_state()
            return False

        # timeout
        now = time.time()
        if now - self._ball_last_seen > self.BALL_ABSENT_TIMEOUT:
            self._reset_state()
            return False

        # distances
        forward, lateral, horizontal_degree = self._ball_distances
        if forward > self.CHASE_EXIT_FORWARD_THRESHOLD:
            self._reset_state()
            return False

        # position
        if math.fabs(self._position.x) > self._max_x:
            self._cmd.chassis_wheel(0, 0, 0, 0)
            self._cmd.led_control(rm.LED_BOTTOM_FRONT, rm.LED_EFFECT_BLINK, 0, 0, 255)
            self._cmd.led_control(rm.LED_BOTTOM_BACK, rm.LED_EFFECT_BLINK, 0, 0, 255)
            return False

        if self._position.y > self._max_y and lateral > self.DISTANCE_EPS:
            self._cmd.chassis_wheel(0, 0, 0, 0)
            self._cmd.led_control(rm.LED_BOTTOM_RIGHT, rm.LED_EFFECT_BLINK, 0, 0, 255)
            return False
        if self._position.y < -self._max_y and -lateral > self.DISTANCE_EPS:
            self._cmd.chassis_wheel(0, 0, 0, 0)
            self._cmd.led_control(rm.LED_BOTTOM_LEFT, rm.LED_EFFECT_BLINK, 0, 0, 255)
            return False

        return True

    def _chase(self):
        ok = self._chase_kick_check()
        if not ok:
            return

        forward, lateral, horizontal_degree = self._ball_distances
        # can not see ball now
        if forward < 0.25:
            self._cmd.chassis_wheel(0, 0, 0, 0)
            return
        vy = self._y_pid(lateral)
        vy = 0 if math.fabs(vy) < 0.1 else vy
        if vy != 0:
            self._cmd.chassis_speed(y=vy)
        else:
            self._cmd.chassis_wheel(0, 0, 0, 0)

    def _kick(self):
        ok = self._chase_kick_check()
        if not ok:
            return

        now = time.time()
        if now - self._ball_last_seen > self.KICK_TIMEOUT:
            return

        forward, lateral, horizontal_degree = self._ball_distances
        vy = self._y_pid(lateral)
        vy = 0 if math.fabs(vy) < 0.1 else vy
        if vy != 0:
            self._cmd.chassis_speed(x=self.DEFAULT_XY_SPEED, y=vy)
        else:
            self._cmd.chassis_speed(x=self.DEFAULT_XY_SPEED)

    def _draw_graph(self):
        if self._ball_distances is None:
            return

        graph = self._graph_base.copy()

        chassis_x = self._position.y
        chassis_y = -self._position.x
        chassis_x_pixel = chassis_x * self._graph_pixel_size
        chassis_y_pixel = chassis_y * self._graph_pixel_size
        cv.rectangle(graph, (int(chassis_x_pixel - self._graph_chassis_width), int(chassis_y_pixel - self._graph_chassis_length)), (int(chassis_x_pixel + self._graph_chassis_width), int(chassis_y_pixel + self._graph_chassis_length)), (0, 0, 255), 2)

        forward, lateral, _ = self._ball_distances
        ball_x_pixel = int((lateral + chassis_x) * self._graph_pixel_size)
        ball_y_pixel = int((chassis_y - forward) * self._graph_pixel_size)

        cv.circle(graph, (ball_x_pixel, ball_y_pixel), self._graph_ball_radius, (0, 255, 0), 2)
        cv.circle(graph, (ball_x_pixel, ball_y_pixel), 1, (0, 128, 128), 2)
        cv.putText(graph, self._state.name, (20, 20), cv.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        now = time.time()
        cv.putText(graph, 'vision heath: %.2f ms' % ((now - self._ball_last_seen) * 1000 if self._ball_last_seen is not None else -1.0), (20, 70), cv.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv.putText(graph, 'position heath: %.2f ms' % ((now - self._position_last_seen) * 1000 if self._position_last_seen is not None else -1.0), (20, 120), cv.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        cv.putText(graph, 'hit last seen: %.2f ms' % ((now - self._armor_hit_last_seen) * 1000 if self._armor_hit_last_seen is not None else -1.0), (20, 170), cv.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        cv.imshow('graph', graph)
        cv.waitKey(1)

    def _tick(self):
        self._armor_hit_last_seen = None
        self._armor_hit_id = None

        self._drain_push()
        self._drain_event()
        self._drain_vision()

        self._draw_graph()

    def work(self) -> None:
        self._tick()

        if self._state == KeeperState.WATCHING:
            self._watch()
        elif self._state == KeeperState.CHASING:
            self._chase()
        elif self._state == KeeperState.KICKING:
            self._kick()
        else:
            raise ValueError(f'unknown state {self._state}')


def contour_analysis(cnt) -> Tuple[int, int]:
    approx = cv.approxPolyDP(cnt, 0.01 * cv.arcLength(cnt, True), True)
    area = cv.contourArea(cnt)
    return len(approx), area


def biggest_circle_cnt(cnts: List):
    found_cnt = None
    found_edges = 0
    found_area = 0

    for cnt in cnts:
        edges, area = contour_analysis(cnt)
        if edges > 8 \
                and 260 < area < 20000 \
                and edges > found_edges \
                and area > found_area:
            found_edges = edges
            found_area = area
            found_cnt = cnt

    return found_cnt


def vision(frame, logger: logging.Logger) -> Optional[Tuple[float, float, float]]:
    processed = cv.GaussianBlur(frame, (11, 11), 0)
    processed = cv.cvtColor(processed, cv.COLOR_BGR2HSV)

    mask = cv.inRange(processed, GREEN_LOWER, GREEN_UPPER)
    mask = cv.morphologyEx(mask, cv.MORPH_OPEN, None)
    cnts, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)

    ball_cnt = biggest_circle_cnt(cnts)
    if ball_cnt is None:
        cv.putText(frame, 'no ball detected', (50, 20), cv.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        cv.imshow('vision', frame)
        cv.waitKey(1)
        return None

    (x, y), pixel_radius = cv.minEnclosingCircle(ball_cnt)
    distance = measure.pinhole_distance(BALL_ACTUAL_RADIUS, pixel_radius)
    forward, lateral, horizontal_degree = measure.distance_decomposition(x, distance)
    cv.circle(frame, (int(x), int(y)), int(pixel_radius), (0, 255, 0), 2)
    cv.circle(frame, (int(x), int(y)), 1, (0, 0, 255), 2)
    cv.putText(frame, 'forward: %.1f cm' % (forward * 100), (50, 20), cv.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    cv.putText(frame, 'lateral: %.1f cm' % (lateral * 100), (50, 70), cv.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

    cv.imshow('vision', frame)
    cv.waitKey(1)

    return forward, lateral, horizontal_degree


@click.group()
@click.option('--ip', default='', type=str, help='(Optional) IP of Robomaster EP')
@click.option('--timeout', default=10.0, type=float, help='(Optional) Timeout for commands')
@click.option('--max-width', default=1.0, type=float, help='(Optional) Field width')
@click.option('--max-depth', default=1.0, type=float, help='(Optional) Field depth')
@click.pass_context
def cli(ctx: click.Context, ip: str, timeout: float, max_width: float, max_depth: float):
    ctx.ensure_object(dict)
    ctx.obj['manager']: mp.managers.SyncManager = CTX.Manager()
    ctx.obj['ip']: str = ip
    ctx.obj['timeout']: float = timeout
    ctx.obj['max_width']: float = max_width
    ctx.obj['max_depth']: float = max_depth


@cli.command()
@click.pass_context
def ensure_field(ctx: click.Context):
    pass


@cli.command()
@click.pass_context
def play(ctx: click.Context):
    manager: mp.managers.SyncManager = CTX.Manager()

    with manager:
        hub = rm.Hub()
        cmd = rm.Commander(ip=ctx.obj['ip'], timeout=ctx.obj['timeout'])
        ip = cmd.get_ip()

        # queues
        vision_queue = manager.Queue(QUEUE_SIZE)
        push_queue = manager.Queue(QUEUE_SIZE)
        event_queue = manager.Queue(QUEUE_SIZE)

        # vision
        cmd.stream(True)
        hub.worker(rm.Vision, 'vision', (vision_queue, ip, vision))

        # push and event
        cmd.chassis_push_on(position_freq=SYSTEM_FREQUENCY, attitude_freq=SYSTEM_FREQUENCY)
        cmd.armor_sensitivity(10)
        cmd.armor_event(rm.ARMOR_HIT, True)
        hub.worker(rm.PushListener, 'chassis-push', (push_queue,))
        hub.worker(rm.EventListener, 'armor-event', (event_queue, ip))

        # controller
        hub.worker(KeeperMind, 'keeper', (ip, vision_queue, push_queue, event_queue, ctx.obj['max_width'], ctx.obj['max_depth'], ctx.obj['timeout']))

        hub.run()


if __name__ == '__main__':
    cli(obj={})
