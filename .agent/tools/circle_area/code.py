import math

async def circle_area(radius: float) -> float:
    """计算圆的面积。参数 radius 为圆的半径（数值，大于等于 0），返回面积 = π * r²。"""
    if radius < 0:
        raise ValueError("半径不能为负数")
    return math.pi * radius * radius
