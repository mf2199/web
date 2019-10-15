
import logging
import random

from django.conf import settings
from django.db.models import Count
from django.shortcuts import redirect, render
from django.utils import timezone

from dashboard.models import Activity
from inbox.utils import send_notification_to_user
from kudos.models import BulkTransferCoupon, BulkTransferRedemption, Token
from kudos.views import get_profile
from perftools.models import JSONStore
from quests.models import Quest, QuestAttempt, QuestPointAward

logger = logging.getLogger(__name__)

max_ref_depth = 4

def record_quest_activity(quest, associated_profile, event_name, override_created=None):
    """
    Creates activity feeds from people doing quests.
    """

    kwargs = {
        'created_on': timezone.now() if not override_created else override_created,
        'activity_type': event_name,
        'profile': associated_profile,
        'metadata': {
            'quest_url': quest.url,
            'quest_title': quest.title,
            'quest_reward': quest.enemy_img_url if quest.enemy_img_url else None,
        }
    }

    try:
        Activity.objects.create(**kwargs)
    except Exception as e:
        logger.exception(e)


def record_award_helper(qa, profile, layer=1):
    """
    Awards point awards (and referral rewards) to a winner
    """

    #max depth
    if layer > max_ref_depth:
        return

    # record points
    value = qa.quest.value/(2**(layer-1))
    QuestPointAward.objects.create(
        questattempt=qa,
        profile=profile,
        value=value
        )

    # record kudos
    if layer > 1 or settings.DEBUG:
        gitcoinbot = get_profile('gitcoinbot')
        quest = qa.quest
        btc = BulkTransferCoupon.objects.create(
            token=quest.kudos_reward,
            tag='quest',
            num_uses_remaining=1,
            num_uses_total=1,
            current_uses=0,
            secret=random.randint(10**19, 10**20),
            comments_to_put_in_kudos_transfer=f"Congrats on beating the '{quest.title}' Gitcoin Quest",
            sender_profile=gitcoinbot,
            metadata={
                'recipient': profile.pk,
            }
            )
        cta_url = btc.url
        cta_text = 'Redeem Kudos'
        msg_html = f"@{qa.profile.handle} just beat '{qa.quest.title}'.  You earned {round(value,2)} quest points & a kudos for referring them."
        send_notification_to_user(gitcoinbot.user, profile.user, cta_url, cta_text, msg_html)

    # recursively record points for your referals quest
    if profile.referrer:
        return record_award_helper(qa, profile.referrer, layer+1)


def get_base_quest_view_params(profile, quest):
    """
    Gets the base quest view params
    """
    attempts = quest.attempts.filter(profile=profile) if profile else quest.attempts.none()
    params = {
        'quest': quest,
        'hide_col': True,
        'attempt_count': attempts.count() + 1,
        'success_count': attempts.filter(success=True).count(),
        'body_class': 'quest_battle',
        'title': "Quest: " + quest.title + (f" (and win a *{quest.kudos_reward.humanized_name}* Kudos)" if quest.kudos_reward else ""),
        'avatar_url': quest.enemy_img_url,
        'card_desc': quest.description,
        'quest_json': quest.to_json_dict(exclude="questions"),
    }
    return params

def get_active_attempt_if_any(profile, quest, state=None):
    """
    Gets the active quest attempt if any
    """
    active_attempts = QuestAttempt.objects.filter(
        quest=quest,
        profile=profile,
        success=False,
        created_on__gt=(timezone.now()-timezone.timedelta(minutes=quest.cooldown_minutes))
    )
    if state:
        active_attempts = active_attempts.filter(state=state)
    active_attempt = active_attempts.order_by('-pk').first()
    return active_attempt


def process_start(request, quest):
    """
    Processes the start of the quest oh behalf of the user
    """
    QuestAttempt.objects.create(
        quest=quest,
        success=False,
        profile=request.user.profile,
        state=0,
        )
    record_quest_activity(quest, request.user.profile, 'played_quest')


def process_win(request, qa):
    """
    Processes the win on behalf of the user
    """
    quest = qa.quest
    was_already_beaten = quest.is_beaten(request.user)
    first_time_beaten = not was_already_beaten
    record_quest_activity(quest, request.user.profile, 'beat_quest')
    btcs = BulkTransferCoupon.objects.filter(
        token=quest.kudos_reward,
        tag='quest',
        metadata__recipient=request.user.profile.pk)
    btc = None
    if btcs.exists():
        btc = btcs.first()
    else:
        btc = BulkTransferCoupon.objects.create(
            token=quest.kudos_reward,
            tag='quest',
            num_uses_remaining=1,
            num_uses_total=1,
            current_uses=0,
            secret=random.randint(10**19, 10**20),
            comments_to_put_in_kudos_transfer=f"Congrats on beating the '{quest.title}' Gitcoin Quest",
            sender_profile=get_profile('gitcoinbot'),
            metadata={
                'recipient': request.user.profile.pk,
            },
            )
    prize_url = f"{btc.url}?cb=ref:{request.user.profile.ref_code}&tweet_url={settings.BASE_URL}{quest.url}&tweet=I just won a {quest.kudos_reward.humanized_name} Kudos by beating the '{quest.title} Quest' on @gitcoin quests."
    qa.success = True
    qa.save()
    if first_time_beaten:
        record_award_helper(qa, qa.profile)
    return prize_url


def get_leaderboard(max_entries=25):
    return JSONStore.objects.filter(view='quests', key='leaderboard').order_by('-pk').first().data


def generate_leaderboard(max_entries=25):
    """
    Gets the leaderboard that will be shown on /quests landing page
    """

    #setup
    kudos_to_show_per_leaderboard_entry = 5
    leaderboard = {}

    #pull totals for each qpa
    for qpa in QuestPointAward.objects.all():
        key = qpa.profile.handle
        if key not in leaderboard.keys():
            leaderboard[key] = 0
        leaderboard[key] += qpa.value
    leaderboard = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)

    # add kudos to each leadervoard item
    return_leaderboard = []
    reward_kudos = {
        1: 621,
        2: 618,
        3: 622,
    }

    # assemble leaderboard
    counter = 0
    for ele in leaderboard:
        counter += 1
        btr = BulkTransferRedemption.objects.filter(coupon__tag='quest',redeemed_by__handle=ele[0]).order_by('-created_on')
        kudii = list(set([(_ele.coupon.token.img_url, _ele.coupon.token.humanized_name) for _ele in btr]))[:kudos_to_show_per_leaderboard_entry]
        display_pts = int(ele[1]) if not ele[1] % 1 else round(ele[1],1)
        reward_kudos_pk = reward_kudos.get(counter)
        reward_kudos_url = Token.objects.get(pk=reward_kudos_pk) if reward_kudos_pk else None
        this_ele = [ele[0], display_pts, kudii, reward_kudos_url, counter]
        return_leaderboard.append(this_ele)

    # return values
    leaderboard_hero = return_leaderboard
    if len(leaderboard) < 3:
        leaderboard_hero = []
    else:
        leaderboard = leaderboard[3:]
        # swap locationms of 1 and 2
        tmp = None
        tmp = leaderboard_hero[0]
        leaderboard_hero[0] = leaderboard_hero[1]
        leaderboard_hero[1] = tmp
        leaderboard_hero = leaderboard_hero[:3]
        
    return_leaderboard = return_leaderboard[:max_entries]
    return return_leaderboard, leaderboard_hero
